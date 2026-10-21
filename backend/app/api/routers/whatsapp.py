"""WhatsApp inbox, verified receipt webhook and explicit review/retry actions."""
import hmac
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api.routers.candidates import lock_assignment_cursor
from app.core.config import get_settings
from app.core.deps import current_user, require_role
from app.db.models import AuditEntry, Candidate, Role, User, WhatsAppChannel, WhatsAppMessage
from app.db.session import get_db
from app.integrations.whatsapp import missing_settings, verify_signature
from app.services.document_extraction import request_extraction_retry
from app.services.document_storage import file_response
from app.services.whatsapp_intake import attach_original, receive_payload

router = APIRouter(prefix="/api/whatsapp", tags=["WhatsApp"])
admin = require_role(Role.admin)


@router.get("/webhook", response_class=PlainTextResponse)
def verify(mode: str = Query(alias="hub.mode"), token: str = Query(alias="hub.verify_token"), challenge: str = Query(alias="hub.challenge")):
    settings = get_settings()
    if not settings.whatsapp_enabled or not settings.whatsapp_verify_token or mode != "subscribe" or not hmac.compare_digest(token.encode(), settings.whatsapp_verify_token.encode()):
        raise HTTPException(403, "Webhook verification failed")
    return challenge


@router.post("/webhook")
async def webhook(request: Request, db: Session = Depends(get_db)):
    settings = get_settings()
    if not settings.whatsapp_enabled or missing_settings(settings):
        raise HTTPException(503, "WhatsApp is not configured")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 1024 * 1024:
            raise HTTPException(413, "Webhook payload is too large")
    if not verify_signature(body, request.headers.get("X-Hub-Signature-256"), settings.whatsapp_app_secret):
        raise HTTPException(401, "Webhook signature is invalid")
    try:
        payload = json.loads(body)
        if not isinstance(payload, dict) or payload.get("object") != "whatsapp_business_account":
            raise ValueError()
        count = await run_in_threadpool(receive_payload, db, payload, settings)
    except (ValueError, TypeError, AttributeError, OverflowError, OSError):
        db.rollback()
        raise HTTPException(400, "Webhook payload is invalid") from None
    return {"received": count}


@router.get("")
def inbox(q: str = "", page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), status: str | None = None,
          db: Session = Depends(get_db), user: User = Depends(current_user)):
    settings = get_settings()
    conditions = []
    if q.strip():
        pattern = f"%{q.strip()}%"
        conditions.append(or_(WhatsAppMessage.sender_number.ilike(pattern), WhatsAppMessage.sender_name.ilike(pattern), WhatsAppMessage.body.ilike(pattern), WhatsAppMessage.file_name.ilike(pattern)))
    if status: conditions.append(WhatsAppMessage.status == status)
    total = db.scalar(select(func.count()).select_from(WhatsAppMessage).where(*conditions)) or 0
    rows = db.scalars(select(WhatsAppMessage).where(*conditions).order_by(WhatsAppMessage.received_at.desc(), WhatsAppMessage.id).offset((page - 1) * page_size).limit(page_size))
    channels = db.scalars(select(WhatsAppChannel))
    return {"configured": settings.whatsapp_enabled and not missing_settings(settings), "missingSettings": missing_settings(settings) if user.role == Role.admin else [],
        "enabled": settings.whatsapp_enabled, "configuredPhoneNumberId": settings.whatsapp_phone_number_id if user.role == Role.admin else None,
        "webhookPath": "/api/whatsapp/webhook", "total": total, "page": page, "pageSize": page_size,
        "channels": [{"id": row.phone_number_id, "displayNumber": row.display_number, "enabled": row.enabled, "autoIngest": row.auto_ingest,
                      "lastReceivedAt": row.last_received_at, "workerSeenAt": row.worker_seen_at, "lastError": row.last_error} for row in channels],
        "messages": [{"id": row.id, "phoneNumberId": row.phone_number_id, "senderNumber": row.sender_number, "senderName": row.sender_name, "type": row.message_type,
                      "body": row.body, "fileName": row.file_name, "hasFile": bool(row.object_key), "receivedAt": row.received_at,
                      "status": row.status, "attempts": row.attempts, "lastError": row.last_error, "candidateId": row.candidate_id} for row in rows]}


@router.get("/health")
def health(db: Session = Depends(get_db), user: User = Depends(admin)):
    """Operational counters only; the dashboard does not need message bodies."""
    settings = get_settings()
    counts = dict(db.execute(select(WhatsAppMessage.status, func.count()).group_by(WhatsAppMessage.status)).all())
    return {"enabled": settings.whatsapp_enabled,
            "configured": settings.whatsapp_enabled and not missing_settings(settings),
            "counts": {name: counts.get(name, 0) for name in ("received", "pending", "processing", "retry", "needs_review", "completed", "ignored", "failed")},
            "channels": [{"id": row.phone_number_id, "displayNumber": row.display_number,
                          "enabled": row.enabled, "autoIngest": row.auto_ingest,
                          "lastReceivedAt": row.last_received_at, "workerSeenAt": row.worker_seen_at,
                          "lastError": row.last_error} for row in db.scalars(select(WhatsAppChannel).order_by(WhatsAppChannel.phone_number_id))]}


class ChannelIn(BaseModel):
    autoIngest: bool


@router.post("/channels/connect")
def connect_channel(db: Session = Depends(get_db), user: User = Depends(admin)):
    settings = get_settings()
    if not settings.whatsapp_enabled or missing_settings(settings):
        raise HTTPException(503, "Configure the WhatsApp Business credentials on the server before connecting.")
    phone_id = settings.whatsapp_phone_number_id
    db.execute(insert(WhatsAppChannel).values(phone_number_id=phone_id, enabled=True)
               .on_conflict_do_update(index_elements=["phone_number_id"], set_={"enabled": True, "last_error": None}))
    db.add(AuditEntry(actor_id=user.id, action="Connected WhatsApp channel", entity_type="whatsapp_channel", entity_id=phone_id))
    db.commit()
    return {"id": phone_id, "enabled": True}


@router.post("/channels/{phone_number_id}/disconnect")
def disconnect_channel(phone_number_id: str, db: Session = Depends(get_db), user: User = Depends(admin)):
    channel = db.scalar(select(WhatsAppChannel).where(WhatsAppChannel.phone_number_id == phone_number_id).with_for_update())
    if channel is None:
        raise HTTPException(404, "Channel not found")
    channel.enabled = False
    db.add(AuditEntry(actor_id=user.id, action="Disconnected WhatsApp channel", entity_type="whatsapp_channel", entity_id=phone_number_id,
                      meta={"retainedMessages": True, "queuedWorkPaused": True}))
    db.commit()
    return {"id": phone_number_id, "enabled": False}


@router.patch("/channels/{phone_number_id}")
def update_channel(phone_number_id: str, body: ChannelIn, db: Session = Depends(get_db), user: User = Depends(admin)):
    channel = db.get(WhatsAppChannel, phone_number_id)
    if channel is None: raise HTTPException(404, "Channel not found")
    if not channel.enabled: raise HTTPException(409, "Reconnect this channel before changing automatic intake.")
    channel.auto_ingest = body.autoIngest
    db.add(AuditEntry(actor_id=user.id, action="Changed WhatsApp auto-ingest", entity_type="whatsapp_channel", target=channel.display_number, meta=body.model_dump()))
    db.commit()
    return {"ok": True}


@router.get("/messages/{message_id}/file")
def attachment(message_id: str, download: bool = False, db: Session = Depends(get_db), user: User = Depends(current_user)):
    row = db.get(WhatsAppMessage, message_id)
    if not row or not row.object_key: raise HTTPException(404, "The original media has not been retrieved yet")
    try: return file_response(get_settings(), row.object_key, row.file_name or "WhatsApp document", row.content_type or "application/octet-stream", download)
    except Exception: raise HTTPException(503, "The original document is temporarily unavailable") from None


@router.post("/messages/{message_id}/retry")
def retry(message_id: str, db: Session = Depends(get_db), user: User = Depends(admin)):
    row = db.scalar(select(WhatsAppMessage).where(WhatsAppMessage.id == message_id).with_for_update())
    if row is None: raise HTTPException(404, "Message not found")
    channel = db.get(WhatsAppChannel, row.phone_number_id)
    if not channel or not channel.enabled: raise HTTPException(409, "Reconnect this channel before queuing document processing.")
    if row.status == "completed": raise HTTPException(409, "This message is already linked to a candidate")
    if row.status in {"pending", "processing", "retry"}: raise HTTPException(409, "This message is already queued for processing")
    if not row.media_id: raise HTTPException(422, "This message has no supported document or image")
    state = dict(row.processing_state or {})
    extraction = dict(state.get("extraction", {}))
    request_extraction_retry(extraction, automatic=False)
    state["extraction"] = extraction
    state.pop("output", None); state.pop("documents", None)
    row.processing_state, row.status, row.attempts, row.last_error = state, "pending", 0, None
    row.next_attempt_at = datetime.now(timezone.utc)
    db.add(AuditEntry(actor_id=user.id, action="Retried WhatsApp intake", entity_type="whatsapp_message", entity_id=row.id))
    db.commit()
    return {"ok": True}


class ResolveIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    candidateId: str = Field(min_length=1, max_length=36)
    reason: str = Field(min_length=3, max_length=2000)


@router.post("/messages/{message_id}/resolve")
def resolve(message_id: str, body: ResolveIn, db: Session = Depends(get_db), user: User = Depends(admin)):
    lock_assignment_cursor(db)
    candidate = db.scalar(select(Candidate).where(Candidate.id == body.candidateId).with_for_update())
    if candidate is None: raise HTTPException(404, "Candidate not found")
    row = db.scalar(select(WhatsAppMessage).where(WhatsAppMessage.id == message_id).with_for_update())
    if row is None: raise HTTPException(404, "Message not found")
    if row.status in {"pending", "processing", "retry"}: raise HTTPException(409, "Wait for document processing before resolving this message")
    if row.candidate_id: raise HTTPException(409, "This message has already been resolved")
    if not row.object_key:
        raise HTTPException(422, "Retrieve the original document before linking this message to a candidate.")
    attach_original(db, row, candidate)
    row.candidate_id, row.status, row.last_error = candidate.id, "completed", None
    db.add(AuditEntry(actor_id=user.id, action="Resolved WhatsApp intake", entity_type="candidate", entity_id=candidate.id,
                      target=candidate.name, meta={"messageId": row.id, "reason": body.reason}))
    db.commit()
    return {"candidateId": candidate.id}
