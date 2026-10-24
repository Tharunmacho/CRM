"""Shared WhatsApp conversations with authorized, durable outbound actions."""
from datetime import datetime
import hashlib
import re
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, literal, or_, select, union_all
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, load_only

from app.core.config import get_settings
from app.core.deps import current_user, require_role
from app.db.models import (AuditEntry, Candidate, Role, User, WhatsAppChannel, WhatsAppConversation,
    WhatsAppMessage, WhatsAppSendAttempt, WhatsAppTemplate)
from app.db.session import get_db, writes_on_get
from app.integrations import whatsapp as provider
from app.services import whatsapp_chat as chat
from app.services.parallel_reads import parallel_reads
from app.services.document_storage import file_response, put_bytes

router = APIRouter(prefix="/api/whatsapp", tags=["WhatsApp chat"])
supervisor = require_role(Role.admin, Role.manager)


class OpenIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    phoneNumberId: str = Field(min_length=1, max_length=80)
    peerNumber: str = Field(min_length=7, max_length=40)
    candidateId: str | None = Field(default=None, max_length=36)


class CandidateIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    candidateId: str | None = Field(default=None, max_length=36)
    reason: str = Field(min_length=3, max_length=2000)


class TemplateIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=512)
    language: str = Field(min_length=1, max_length=30)
    parameters: list[str] = Field(default_factory=list, max_length=50)


class SendIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    requestKey: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    type: Literal["text", "template"] = "text"
    body: str = Field(default="", max_length=4096)
    template: TemplateIn | None = None


class SyncIn(BaseModel):
    phoneNumberId: str = Field(min_length=1, max_length=80)


@router.get("/conversations")
def conversations(response: Response, q: str = Query("", max_length=200), page: int = Query(1, ge=1),
                  page_size: int = Query(20, ge=1, le=100), db: Session = Depends(get_db), user: User = Depends(current_user)):
    settings = get_settings()
    conditions = []
    if q.strip():
        pattern = "%" + q.strip().replace("%", "\\%").replace("_", "\\_") + "%"
        conditions.append(or_(WhatsAppConversation.peer_number.ilike(pattern), WhatsAppConversation.display_name.ilike(pattern), Candidate.name.ilike(pattern)))
    base = select(WhatsAppConversation, Candidate, WhatsAppChannel).outerjoin(Candidate, Candidate.id == WhatsAppConversation.candidate_id).join(
        WhatsAppChannel, WhatsAppChannel.phone_number_id == WhatsAppConversation.phone_number_id).options(
        load_only(Candidate.id, Candidate.ref, Candidate.name, Candidate.owner_id),
        load_only(WhatsAppChannel.phone_number_id, WhatsAppChannel.enabled))
    total, rows = parallel_reads(
        db,
        lambda session: session.scalar(select(func.count()).select_from(WhatsAppConversation).outerjoin(Candidate, Candidate.id == WhatsAppConversation.candidate_id).where(*conditions)) or 0,
        lambda session: session.execute(base.where(*conditions).order_by(WhatsAppConversation.last_message_at.desc().nullslast(), WhatsAppConversation.id)
            .offset((page - 1) * page_size).limit(page_size)).all(),
    )
    response.headers["Cache-Control"] = "private, no-store"
    return {"configured": settings.whatsapp_enabled and not provider.missing_settings(settings), "enabled": settings.whatsapp_enabled,
        "missingSettings": provider.missing_settings(settings) if user.role == Role.admin else [], "page": page, "pageSize": page_size,
        "total": total, "conversations": [chat.conversation_json(db, row, user, settings, candidate=candidate, channel=channel) for row, candidate, channel in rows]}


@router.get("/candidates/{candidate_id}/conversation")
def candidate_conversation(candidate_id: str, response: Response, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """The conversation linked to a candidate, for the profile's WhatsApp button (one cheap read)."""
    response.headers["Cache-Control"] = "private, no-store"
    row = chat.conversation_for_candidate(db, candidate_id)
    return {"conversation": chat.conversation_json(db, row, user, get_settings()) if row else None}


@router.post("/conversations/open")
def open_chat(body: OpenIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    row = chat.open_conversation(db, body.phoneNumberId, body.peerNumber, candidate_id=body.candidateId, actor=user)
    db.commit()
    return {"conversation": chat.conversation_json(db, row, user, get_settings())}


@router.patch("/conversations/{conversation_id}/candidate")
def link_candidate(conversation_id: str, body: CandidateIn, db: Session = Depends(get_db), user: User = Depends(supervisor)):
    row = chat.get_conversation(db, conversation_id, lock=True)
    if body.candidateId and db.scalar(select(Candidate.id).where(Candidate.id == body.candidateId)) is None: raise HTTPException(404, "Candidate not found")
    previous, row.candidate_id = row.candidate_id, body.candidateId
    db.add(AuditEntry(actor_id=user.id, action="Linked WhatsApp conversation candidate", entity_type="whatsapp_conversation",
        entity_id=row.id, meta={"fromCandidateId": previous, "candidateId": body.candidateId, "reason": body.reason}))
    db.commit()
    return {"conversation": chat.conversation_json(db, row, user, get_settings())}


def _inbound_json(row):
    return {"id": row.id, "direction": "inbound", "type": row.message_type, "body": row.body,
        "at": row.received_at, "state": row.status, "providerMessageId": row.provider_message_id, "candidateId": row.candidate_id,
        "fileName": row.file_name, "contentType": row.content_type, "hasFile": bool(row.object_key or row.media_id),
        "fileUrl": f"/api/whatsapp/chat-media/{row.id}" if row.media_id or row.object_key else None}


@router.get("/conversations/{conversation_id}/messages")
def messages(conversation_id: str, response: Response, before: str | None = Query(None, max_length=160),
             limit: int = Query(40, ge=1, le=100), db: Session = Depends(get_db), user: User = Depends(current_user)):
    conversation = chat.get_conversation(db, conversation_id)
    inbound = select(WhatsAppMessage.id, literal("inbound").label("direction"), WhatsAppMessage.received_at.label("at")).where(
        WhatsAppMessage.phone_number_id == conversation.phone_number_id, WhatsAppMessage.sender_number == conversation.peer_number)
    outbound = select(WhatsAppSendAttempt.id, literal("outbound").label("direction"), WhatsAppSendAttempt.created_at.label("at")).where(
        WhatsAppSendAttempt.conversation_id == conversation.id)
    timeline = union_all(inbound, outbound).subquery("timeline")
    query = select(timeline)
    if before:
        try:
            timestamp, direction, identifier = before.split("|", 2)
            stamp = datetime.fromisoformat(timestamp)
            if stamp.tzinfo is None or direction not in {"inbound", "outbound"} or not identifier: raise ValueError()
        except (ValueError, TypeError): raise HTTPException(422, "We could not load more messages. Reload the conversation and try again.") from None
        query = query.where(or_(timeline.c.at < stamp, (timeline.c.at == stamp) & (timeline.c.direction < direction),
            (timeline.c.at == stamp) & (timeline.c.direction == direction) & (timeline.c.id < identifier)))
    settings = get_settings()
    # The page and the conversation header (candidate, channel, send permission) are independent.
    page, header = parallel_reads(
        db,
        lambda session: session.execute(query.order_by(timeline.c.at.desc(), timeline.c.direction.desc(), timeline.c.id.desc()).limit(limit + 1)).mappings().all(),
        lambda session: chat.conversation_json(session, conversation, user, settings),
    )
    ids = {direction: [r["id"] for r in page[:limit] if r["direction"] == direction] for direction in ("inbound", "outbound")}

    def read_rows(model, direction, serializer):
        def read(session):
            if not ids[direction]:
                return {}
            statement = select(model).where(model.id.in_(ids[direction]))
            if model is WhatsAppMessage:
                statement = statement.options(load_only(WhatsAppMessage.id, WhatsAppMessage.message_type, WhatsAppMessage.body,
                    WhatsAppMessage.received_at, WhatsAppMessage.status, WhatsAppMessage.provider_message_id, WhatsAppMessage.candidate_id,
                    WhatsAppMessage.file_name, WhatsAppMessage.content_type, WhatsAppMessage.object_key, WhatsAppMessage.media_id))
            return {(direction, row.id): serializer(row) for row in session.scalars(statement)}
        return read

    rows = {}
    for found in parallel_reads(db, read_rows(WhatsAppMessage, "inbound", _inbound_json),
                                read_rows(WhatsAppSendAttempt, "outbound", chat.outbound_json)):
        rows.update(found)
    last = page[limit - 1] if len(page) > limit else None
    response.headers["Cache-Control"] = "private, no-store"
    return {"conversation": header,
        "messages": [rows[(row["direction"], row["id"])] for row in reversed(page[:limit])],
        "nextBefore": f"{last['at'].isoformat()}|{last['direction']}|{last['id']}" if last else None}


def _queue(db, user, conversation_id, body, request_key, *, kind="text", template=None, media=None):
    conversation = chat.get_conversation(db, conversation_id)
    try:
        row = chat.enqueue(db, conversation.phone_number_id, conversation.peer_number, body, request_key,
            actor_id=user.id, conversation_id=conversation_id, kind=kind, template=template, media=media, settings=get_settings())
        db.commit()
        return {"message": chat.outbound_json(row)}
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "This message was already sent. Reload the conversation to see it.") from None


@router.post("/conversations/{conversation_id}/send")
def send(conversation_id: str, body: SendIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    if body.type == "text" and body.template is not None: raise HTTPException(422, "Text messages cannot include a template.")
    if body.type == "template" and body.body: raise HTTPException(422, "Fill in the approved template instead of typing your own message.")
    return _queue(db, user, conversation_id, body.body, body.requestKey, kind=body.type,
        template=body.template.model_dump() if body.template else None)


@router.post("/conversations/{conversation_id}/send-media")
def send_media(conversation_id: str, requestKey: str = Form(...), caption: str = Form(""), file: UploadFile = File(...),
               db: Session = Depends(get_db), user: User = Depends(current_user)):
    conversation = chat.get_conversation(db, conversation_id)
    allowed, reason = chat._send_permission(db, conversation, user)
    if not allowed: raise HTTPException(403, reason)
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", requestKey): raise HTTPException(422, "Something went wrong while sending. Reload the page and try again.")
    content_type = (file.content_type or "").lower().strip()
    mime = content_type.split(";", 1)[0]
    supported = provider.CHAT_TYPES.get(mime)
    if not supported: raise HTTPException(422, "Choose a photo, PDF, Office document, audio or video file.")
    payload = file.file.read(supported[1] + 1)
    if not payload or len(payload) > supported[1]: raise HTTPException(422, f"The file must be nonempty and no larger than {supported[1] // (1024 * 1024)} MB.")
    filename = re.sub(r"[^A-Za-z0-9._ -]", "-", (file.filename or "attachment").replace("\\", "/").rsplit("/", 1)[-1])[:200] or "attachment"
    digest = hashlib.sha256(payload).hexdigest()
    media = {"file_name": filename, "content_type": content_type, "content_hash": digest, "size_bytes": len(payload)}
    existing = db.scalar(select(WhatsAppSendAttempt).where(WhatsAppSendAttempt.request_key == requestKey))
    if existing:
        # enqueue verifies the exact original fingerprint and actor. Recovery
        # never uploads a second file, even after the reply window has expired.
        media["object_key"] = existing.object_key
        return _queue(db, user, conversation_id, caption, requestKey, kind=supported[0], media=media)
    chat._channel(db, conversation.phone_number_id, get_settings(), sending=True)
    if not chat.window(conversation)[0]: raise HTTPException(409, "The 24-hour reply time has ended. Send an approved template instead.")
    if len(caption.strip()) > 1024: raise HTTPException(422, "Keep the media caption within 1,024 characters.")
    if supported[0] == "audio" and caption.strip(): raise HTTPException(422, "Audio messages do not support captions.")
    # The deterministic immutable key also makes an upload replay safe. It never
    # contains the user's filename or any path from the request.
    media["object_key"] = put_bytes(get_settings(), f"whatsapp/outbound/{conversation.id}/{requestKey}/{digest}", payload, content_type)
    return _queue(db, user, conversation_id, caption, requestKey, kind=supported[0], media=media)


@router.get("/conversations/{conversation_id}/send-requests/{request_key}")
def recover_send(conversation_id: str, request_key: str, response: Response, db: Session = Depends(get_db), user: User = Depends(current_user)):
    conversation = chat.get_conversation(db, conversation_id)
    row = db.scalar(select(WhatsAppSendAttempt).where(WhatsAppSendAttempt.conversation_id == conversation_id, WhatsAppSendAttempt.request_key == request_key))
    if row is None: raise HTTPException(404, "Send request not found")
    if row.sender_user_id != user.id and user.role not in (Role.admin, Role.manager):
        raise HTTPException(403, "You can only retry messages that you sent yourself.")
    response.headers["Cache-Control"] = "private, no-store"
    return {"message": chat.outbound_json(row)}


@router.get("/outbound/{attempt_id}")
def outbound(attempt_id: str, response: Response, db: Session = Depends(get_db), user: User = Depends(current_user)):
    row = db.get(WhatsAppSendAttempt, attempt_id)
    if row is None: raise HTTPException(404, "Message not found")
    response.headers["Cache-Control"] = "private, no-store"
    return {"message": chat.outbound_json(row)}


@router.get("/outbound/{attempt_id}/file")
def outbound_file(attempt_id: str, download: bool = False, db: Session = Depends(get_db), user: User = Depends(current_user)):
    row = db.get(WhatsAppSendAttempt, attempt_id)
    if row is None or not row.object_key: raise HTTPException(404, "Attachment not found")
    try: return file_response(get_settings(), row.object_key, row.file_name, row.content_type, download)
    except Exception: raise HTTPException(503, "We cannot open this attachment right now. Try again soon.") from None


@router.get("/chat-media/{message_id}")
@writes_on_get
def inbound_file(message_id: str, download: bool = False, db: Session = Depends(get_db), user: User = Depends(current_user)):
    row = db.scalar(select(WhatsAppMessage).options(load_only(WhatsAppMessage.id, WhatsAppMessage.phone_number_id, WhatsAppMessage.media_id,
        WhatsAppMessage.object_key, WhatsAppMessage.content_hash, WhatsAppMessage.content_type, WhatsAppMessage.file_name))
        .where(WhatsAppMessage.id == message_id).with_for_update())
    if row is None or not (row.media_id or row.object_key): raise HTTPException(404, "Media not found")
    settings = get_settings()
    if not row.object_key:
        chat._channel(db, row.phone_number_id, settings, sending=True)
        try:
            payload, mime = provider.download_media(settings, row.media_id, chat=True)
            digest = hashlib.sha256(payload).hexdigest()
            row.object_key = put_bytes(settings, f"whatsapp/chat-media/{row.id}/{digest}", payload, mime)
            row.content_type, row.content_hash = mime, digest
            db.commit()
        except Exception:
            db.rollback()
            raise HTTPException(503, "This file is no longer available on WhatsApp. Ask the sender to send it again.") from None
    try: return file_response(settings, row.object_key, row.file_name or "WhatsApp attachment", row.content_type or "application/octet-stream", download)
    except Exception: raise HTTPException(503, "We cannot open this attachment right now. Try again soon.") from None


@router.get("/templates")
def templates(phone_number_id: str | None = None, db: Session = Depends(get_db), user: User = Depends(current_user)):
    phone_id = phone_number_id or get_settings().whatsapp_phone_number_id
    rows = db.scalars(select(WhatsAppTemplate).where(WhatsAppTemplate.phone_number_id == phone_id)
        .order_by(WhatsAppTemplate.name, WhatsAppTemplate.language).limit(2000))
    return {"templates": [chat.template_description(row) for row in rows], "canSync": user.role == Role.admin}


@router.post("/templates/sync")
def sync_templates(body: SyncIn, db: Session = Depends(get_db), user: User = Depends(require_role(Role.admin))):
    settings = get_settings()
    if body.phoneNumberId != settings.whatsapp_phone_number_id: raise HTTPException(409, "Choose the WhatsApp number that is set up for this CRM.")
    try: result = chat.sync_templates(db, settings)
    except provider.WhatsAppError as exc: raise HTTPException(503, str(exc)) from None
    db.add(AuditEntry(actor_id=user.id, action="Synchronized WhatsApp templates", entity_type="whatsapp_channel", entity_id=body.phoneNumberId))
    db.commit()
    return {"templates": result, "canSync": True}
