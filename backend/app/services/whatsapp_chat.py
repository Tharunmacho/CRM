"""Durable WhatsApp chat. A committed sending state is never retried automatically."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import load_only

from app.db.models import (AuditEntry, Candidate, Role, User, WhatsAppChannel, WhatsAppConversation,
    WhatsAppDeliveryEvent, WhatsAppMessage, WhatsAppSendAttempt, WhatsAppTemplate)
from app.integrations import whatsapp as provider
from app.services.candidate_identity import normalize_phone
from app.services.document_storage import read_bytes

def utcnow():
    return datetime.now(timezone.utc)


def peer_number(value):
    value = str(value or "").strip()
    if value.startswith("+"): value = value[1:]
    if not re.fullmatch(r"[1-9][0-9]{6,14}", value):
        raise HTTPException(422, "Enter a WhatsApp number with the country code, 7 to 15 digits long.")
    return value


def get_conversation(db, identifier, *, lock=False):
    statement = select(WhatsAppConversation).where(WhatsAppConversation.id == identifier)
    if lock: statement = statement.with_for_update().execution_options(populate_existing=True)
    row = db.scalar(statement)
    if row is None: raise HTTPException(404, "Conversation not found")
    return row


def _channel(db, phone_id, settings=None, *, sending=False):
    channel = db.get(WhatsAppChannel, phone_id)
    if channel is None: raise HTTPException(404, "WhatsApp channel not found")
    if sending and (not channel.enabled or (settings is not None and (not settings.whatsapp_enabled or
            phone_id != settings.whatsapp_phone_number_id or provider.missing_settings(settings)))):
        raise HTTPException(409, "Reconnect and set up this WhatsApp number before you send.")
    return channel


def _send_permission(db, conversation, actor):
    if actor is None or not actor.active:
        return False, "An active employee account is required."
    if actor.role in (Role.admin, Role.manager): return True, None
    candidate = db.scalar(select(Candidate).options(load_only(Candidate.id, Candidate.owner_id)).where(Candidate.id == conversation.candidate_id)) if conversation.candidate_id else None
    if actor.role == Role.staff and candidate and candidate.owner_id == actor.id: return True, None
    return False, "A supervisor or the linked candidate's current recruiter can send messages."


def window(conversation, at=None):
    at = at or utcnow()
    expiry = conversation.last_inbound_at + timedelta(hours=24) if conversation.last_inbound_at else None
    return bool(expiry and at < expiry), expiry


def conversation_json(db, row, actor, settings=None, *, at=None, candidate=None, channel=None):
    candidate = candidate or (db.scalar(select(Candidate).options(load_only(Candidate.id, Candidate.ref, Candidate.name, Candidate.owner_id)).where(Candidate.id == row.candidate_id)) if row.candidate_id else None)
    channel = channel or _channel(db, row.phone_number_id)
    can_send = bool(actor.active and (actor.role in (Role.admin, Role.manager) or
        (actor.role == Role.staff and candidate and candidate.owner_id == actor.id)))
    reason = None if can_send else "A supervisor or the linked candidate's current recruiter can send messages."
    connected = channel.enabled and (settings is None or (settings.whatsapp_enabled and
        row.phone_number_id == settings.whatsapp_phone_number_id and not provider.missing_settings(settings)))
    if not connected: can_send, reason = False, "Reconnect this channel before sending."
    opened, expiry = window(row, at)
    return {"id": row.id, "phoneNumberId": row.phone_number_id, "peerNumber": row.peer_number,
        "displayName": row.display_name, "lastInboundAt": row.last_inbound_at, "lastMessageAt": row.last_message_at,
        "lastPreview": row.last_preview, "candidate": {"id": candidate.id, "ref": candidate.ref,
            "name": candidate.name, "ownerId": candidate.owner_id} if candidate else None,
        "canSend": can_send, "readOnlyReason": reason, "channelEnabled": bool(connected),
        "serviceWindowOpen": opened, "serviceWindowExpiresAt": expiry}


def open_conversation(db, phone_id, number, *, candidate_id=None, actor=None):
    _channel(db, phone_id)
    number = peer_number(number)
    existing = db.scalar(select(WhatsAppConversation).where(WhatsAppConversation.phone_number_id == phone_id,
        WhatsAppConversation.peer_number == number))
    if actor and actor.role == Role.staff:
        if not existing or not _send_permission(db, existing, actor)[0]:
            raise HTTPException(403, "Ask a manager or admin to link this conversation to your candidate first.")
    if candidate_id and (not actor or actor.role not in (Role.admin, Role.manager)):
        raise HTTPException(403, "Only a manager or admin can link a candidate to a conversation.")
    if candidate_id and db.scalar(select(Candidate.id).where(Candidate.id == candidate_id)) is None: raise HTTPException(404, "Candidate not found")
    if existing:
        if candidate_id and existing.candidate_id != candidate_id:
            raise HTTPException(409, "This conversation already exists. Use Link candidate and give a reason to change who it belongs to.")
        return existing
    identifier = str(uuid4())
    db.execute(insert(WhatsAppConversation).values(id=identifier, phone_number_id=phone_id, peer_number=number,
        candidate_id=candidate_id).on_conflict_do_nothing(index_elements=["phone_number_id", "peer_number"]))
    row = db.scalar(select(WhatsAppConversation).where(WhatsAppConversation.phone_number_id == phone_id,
        WhatsAppConversation.peer_number == number))
    if actor:
        db.add(AuditEntry(actor_id=actor.id, action="Opened a WhatsApp chat", entity_type="whatsapp_conversation",
            entity_id=row.id, meta={"candidateId": row.candidate_id}))
    return row


def _digits(expression):
    return func.regexp_replace(expression, r"\D", "", "g")


def candidate_for_number(db, number):
    """The one candidate whose phone or WhatsApp number is this number, else None.

    Numbers are compared the way candidate identity compares them (country code and
    punctuation ignored). An ambiguous number links nobody."""
    wanted = normalize_phone(number)
    if not wanted:
        return None
    tail = f"%{wanted[-10:]}"
    rows = db.scalars(select(Candidate).options(load_only(Candidate.id, Candidate.phone, Candidate.whatsapp, Candidate.owner_id))
        .where(or_(_digits(Candidate.phone).like(tail), _digits(Candidate.whatsapp).like(tail))).limit(10))
    matches = [row for row in rows if wanted in {normalize_phone(row.phone), normalize_phone(row.whatsapp)}]
    return matches[0] if len(matches) == 1 else None


def _link_audit(db, conversation, candidate_id, reason):
    db.add(AuditEntry(action="Linked a WhatsApp chat to a candidate", entity_type="whatsapp_conversation",
        entity_id=conversation.id, meta={"fromCandidateId": None, "candidateId": candidate_id, "reason": reason}))


def link_message_sender(db, message, candidate_id):
    """Link the sender of an intake message to the candidate it created or matched.

    Creates the conversation when the inbound webhook has not, and never replaces an
    existing link: changing one is a supervisor's decision (PATCH .../candidate)."""
    try: number = peer_number(message.sender_number)
    except HTTPException: return None
    stamp = min(message.received_at, message.created_at) if message.created_at else message.received_at
    db.execute(insert(WhatsAppConversation).values(id=str(uuid4()), phone_number_id=message.phone_number_id,
        peer_number=number, display_name=message.sender_name, last_inbound_at=stamp, last_message_at=message.received_at,
        last_preview=(message.body or message.file_name or message.message_type)[:500])
        .on_conflict_do_nothing(index_elements=["phone_number_id", "peer_number"]))
    conversation = db.scalar(select(WhatsAppConversation).where(WhatsAppConversation.phone_number_id == message.phone_number_id,
        WhatsAppConversation.peer_number == number).with_for_update().execution_options(populate_existing=True))
    if conversation is not None and conversation.candidate_id is None:
        conversation.candidate_id = candidate_id
        _link_audit(db, conversation, candidate_id, "Linked automatically from the WhatsApp message that created or matched this candidate.")
    return conversation


def link_conversations_for_candidate(db, candidate):
    """Best effort: attach unlinked conversations whose peer number is this candidate's
    phone or WhatsApp number, so the owner can message a candidate who arrived by another
    route. Never raises into the caller's transaction."""
    try:
        with db.begin_nested():
            wanted = {normalize_phone(candidate.phone), normalize_phone(candidate.whatsapp)} - {""}
            for number in wanted:
                rows = db.scalars(select(WhatsAppConversation).where(WhatsAppConversation.candidate_id.is_(None),
                    WhatsAppConversation.peer_number.like(f"%{number[-10:]}")).with_for_update().limit(10))
                for conversation in rows:
                    if normalize_phone(conversation.peer_number) != number: continue
                    other = candidate_for_number(db, conversation.peer_number)
                    if other is None or other.id != candidate.id: continue
                    conversation.candidate_id = candidate.id
                    _link_audit(db, conversation, candidate.id, "Linked automatically: the candidate's number matches this conversation.")
    except Exception:
        return False
    return True


def conversation_for_candidate(db, candidate_id):
    """The most recently active conversation linked to a candidate, or None."""
    return db.scalar(select(WhatsAppConversation).where(WhatsAppConversation.candidate_id == candidate_id)
        .order_by(WhatsAppConversation.last_message_at.desc().nullslast(), WhatsAppConversation.id).limit(1))


def record_inbound_payload(db, payload, settings, *, at=None):
    """Called only after signature verification and durable inbound receipt."""
    at = at or utcnow()
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            phone_id = str(value.get("metadata", {}).get("phone_number_id", ""))
            if phone_id != settings.whatsapp_phone_number_id: continue
            for message in value.get("messages", []):
                row = db.scalar(select(WhatsAppMessage).options(load_only(WhatsAppMessage.id, WhatsAppMessage.sender_number,
                    WhatsAppMessage.sender_name, WhatsAppMessage.created_at, WhatsAppMessage.received_at, WhatsAppMessage.body,
                    WhatsAppMessage.file_name, WhatsAppMessage.message_type)).where(WhatsAppMessage.provider_message_id == str(message.get("id", "")),
                    WhatsAppMessage.phone_number_id == phone_id))
                if row is None: continue
                try: number = peer_number(row.sender_number)
                except HTTPException: continue
                # Clamp to the durable server receipt time, not the current
                # webhook replay time. Replaying a future timestamp must never
                # extend the customer-service window.
                stamp = min(row.received_at, row.created_at)
                incoming = insert(WhatsAppConversation).values(id=str(uuid4()), phone_number_id=phone_id,
                    peer_number=number, display_name=row.sender_name, last_inbound_at=stamp,
                    last_message_at=row.received_at, last_preview=(row.body or row.file_name or row.message_type)[:500])
                db.execute(incoming.on_conflict_do_nothing(index_elements=["phone_number_id", "peer_number"]))
                conversation = db.scalar(select(WhatsAppConversation).where(WhatsAppConversation.phone_number_id == phone_id,
                    WhatsAppConversation.peer_number == number).with_for_update())
                if not conversation.last_inbound_at or stamp > conversation.last_inbound_at:
                    conversation.last_inbound_at = stamp
                if not conversation.last_message_at or row.received_at >= conversation.last_message_at:
                    conversation.last_message_at, conversation.last_preview = row.received_at, (row.body or row.file_name or row.message_type)[:500]
                    if row.sender_name: conversation.display_name = row.sender_name
                if conversation.candidate_id is None:
                    # Best effort: a sender whose number is already a candidate's is linked at once.
                    try:
                        with db.begin_nested():
                            match = candidate_for_number(db, number)
                            if match is not None:
                                conversation.candidate_id = match.id
                                _link_audit(db, conversation, match.id, "Linked automatically: the sender's number matches this candidate.")
                    except Exception: pass


def _fingerprint(conversation_id, kind, body, template, content_hash, filename, content_type, source_id):
    raw = json.dumps([conversation_id, kind, body, template, content_hash, filename, content_type, source_id], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def enqueue(db, channel_id, recipient, body, request_key, source_message_id=None, actor_id=None, *,
            conversation_id=None, kind="text", template=None, media=None, settings=None, at=None):
    """Create once in caller's transaction. The outbox worker owns dispatch."""
    at = at or utcnow()
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", str(request_key or "")):
        raise HTTPException(422, "Something went wrong while sending. Reload the page and try again.")
    actor = db.scalar(select(User).where(User.id == actor_id).execution_options(populate_existing=True)) if actor_id else None
    if actor_id and (actor is None or not actor.active): raise HTTPException(403, "Your account is no longer active.")
    if actor_id is None:
        source = db.get(WhatsAppMessage, source_message_id) if source_message_id else None
        if not source or source.status != "completed" or source.phone_number_id != channel_id or peer_number(source.sender_number) != peer_number(recipient):
            raise HTTPException(403, "Automatic replies require a completed, verified source receipt.")
    conversation = get_conversation(db, conversation_id, lock=True) if conversation_id else open_conversation(db, channel_id, recipient)
    if not conversation_id: conversation = get_conversation(db, conversation.id, lock=True)
    if conversation.phone_number_id != channel_id or conversation.peer_number != peer_number(recipient):
        raise HTTPException(409, "The person in this conversation changed. Reload the page before you send.")
    if actor_id:
        permitted, reason = _send_permission(db, conversation, actor)
        if not permitted: raise HTTPException(403, reason)
    if actor_id is None:
        receipt_at = min(source.received_at, source.created_at)
        if not conversation.last_inbound_at or receipt_at > conversation.last_inbound_at:
            conversation.last_inbound_at = receipt_at
    media = media or {}
    body = (body or "").strip()
    if kind == "text" and (not body or len(body) > 4096): raise HTTPException(422, "Enter a message of 1–4,096 characters.")
    if kind in {"image", "document", "video", "audio"} and len(body) > 1024:
        raise HTTPException(422, "Keep the media caption within 1,024 characters.")
    if kind == "audio" and body: raise HTTPException(422, "Audio messages do not support captions.")
    if kind not in {"text", "template", "image", "document", "video", "audio"}: raise HTTPException(422, "Unsupported message type.")
    digest = _fingerprint(conversation.id, kind, body, template, media.get("content_hash"), media.get("file_name"), media.get("content_type"), source_message_id)
    existing = db.scalar(select(WhatsAppSendAttempt).where(WhatsAppSendAttempt.request_key == request_key))
    if existing:
        if existing.payload_hash != digest or existing.sender_user_id != actor_id:
            raise HTTPException(409, "This message was already sent with different text. Check the conversation before you send again.")
        return existing
    _channel(db, channel_id, settings, sending=True)
    if kind != "template" and not window(conversation, at)[0]:
        raise HTTPException(409, "The 24-hour reply time has ended. Send an approved template, or wait for a new message from the candidate.")
    if kind == "template":
        approved = _approved_template(db, channel_id, template)
        # Persist the readable approved content, rather than resolving it from
        # mutable template cache during history reads. The request fingerprint
        # above intentionally covers the caller's intent, not display text.
        body = rendered_template(approved, template.get("parameters", []))
    row = WhatsAppSendAttempt(id=str(uuid4()), conversation_id=conversation.id, request_key=request_key,
        sender_user_id=actor_id, source_message_id=source_message_id, payload_hash=digest, message_type=kind,
        body=body, template=template, state="queued", created_at=at, updated_at=at, **media)
    db.add(row); db.flush()
    conversation.last_message_at, conversation.last_preview = at, (body or (template or {}).get("name") or media.get("file_name") or kind)[:500]
    db.add(AuditEntry(actor_id=actor_id, action="Started sending a WhatsApp message", entity_type="whatsapp_send_attempt", entity_id=row.id,
        meta={"conversationId": conversation.id, "type": kind, "sourceMessageId": source_message_id}))
    return row


def template_description(row):
    components = row.components or []
    body = next((str(c.get("text", "")) for c in components if c.get("type", "").upper() == "BODY"), "")
    placeholders = sorted(set(re.findall(r"\{\{(\d+)\}\}", body)), key=int)
    supported, reason = True, None
    if placeholders != [str(i) for i in range(1, len(placeholders) + 1)] or re.search(r"\{\{[^0-9]", body):
        supported, reason = False, "This template uses named or non-sequential parameters."
    for component in components:
        ctype = str(component.get("type", "")).upper()
        if ctype == "HEADER" and (component.get("format") != "TEXT" or "{{" in str(component.get("text", ""))):
            supported, reason = False, "This template needs a dynamic or media header."
        elif ctype == "BUTTONS" and any("{{" in str(b.get("url", "")) or b.get("type") not in {"URL", "PHONE_NUMBER", "QUICK_REPLY"} for b in component.get("buttons", [])):
            supported, reason = False, "This template needs dynamic button parameters."
        elif ctype not in {"HEADER", "BODY", "FOOTER", "BUTTONS"}:
            supported, reason = False, "This template component is not supported by the composer."
    if not body: supported, reason = False, "This template has no text body."
    return {"id": row.id, "name": row.name, "language": row.language, "category": row.category, "status": row.status,
        "body": body, "parameterCount": len(placeholders), "parameters": [{"index": int(n), "label": "Value " + n} for n in placeholders],
        "supported": supported, "unsupportedReason": reason, "syncedAt": row.synced_at}


def rendered_template(row, parameters):
    """Readable snapshot of the supported approved text components."""
    components = row.components or []
    parts = []
    for kind in ("HEADER", "BODY", "FOOTER"):
        component = next((item for item in components if str(item.get("type", "")).upper() == kind), None)
        if component is None or (kind == "HEADER" and component.get("format") != "TEXT"):
            continue
        text = str(component.get("text", ""))
        if kind == "BODY":
            # A callback substitution treats values literally and replaces each
            # original placeholder once, even if a value contains {{2}} itself.
            text = re.sub(r"\{\{(\d+)\}\}", lambda match: parameters[int(match.group(1)) - 1], text)
        if text: parts.append(text)
    return "\n\n".join(parts)


def _approved_template(db, phone_id, value):
    if not isinstance(value, dict): raise HTTPException(422, "Choose an approved template.")
    row = db.scalar(select(WhatsAppTemplate).where(WhatsAppTemplate.phone_number_id == phone_id,
        WhatsAppTemplate.name == value.get("name"), WhatsAppTemplate.language == value.get("language")))
    if row is None or row.status != "APPROVED": raise HTTPException(409, "This template is not approved. Refresh the templates before you send.")
    description = template_description(row)
    parameters = value.get("parameters", [])
    if not description["supported"]: raise HTTPException(422, description["unsupportedReason"])
    if not isinstance(parameters, list) or len(parameters) != description["parameterCount"] or any(not isinstance(v, str) or not v.strip() or len(v) > 1024 for v in parameters):
        raise HTTPException(422, "Fill every template value with 1–1,024 characters.")
    return row


def sync_templates(db, settings, *, client=None, name=None):
    _channel(db, settings.whatsapp_phone_number_id, settings, sending=True)
    rows = provider.fetch_templates(settings, client=client, name=name)
    stamp, seen = utcnow(), set()
    for value in rows:
        if not isinstance(value, dict) or not all(isinstance(value.get(key), str) and value[key] for key in ("name", "language", "status")):
            continue
        key = (value["name"], value["language"])
        seen.add(key)
        row = db.scalar(select(WhatsAppTemplate).where(WhatsAppTemplate.phone_number_id == settings.whatsapp_phone_number_id,
            WhatsAppTemplate.name == key[0], WhatsAppTemplate.language == key[1]).with_for_update())
        if row is None:
            row = WhatsAppTemplate(phone_number_id=settings.whatsapp_phone_number_id, name=key[0], language=key[1]); db.add(row)
        row.status, row.category = value["status"][:30], str(value.get("category", ""))[:30]
        row.components, row.synced_at = value.get("components") or [], stamp
    query = select(WhatsAppTemplate).where(WhatsAppTemplate.phone_number_id == settings.whatsapp_phone_number_id)
    if name: query = query.where(WhatsAppTemplate.name == name)
    for row in db.scalars(query):
        if (row.name, row.language) not in seen: row.status, row.synced_at = "UNAVAILABLE", stamp
    db.flush()
    return [template_description(row) for row in db.scalars(query.order_by(WhatsAppTemplate.name, WhatsAppTemplate.language))]


def outbound_json(row):
    return {"id": row.id, "direction": "outbound", "type": row.message_type, "body": row.body,
        "at": row.created_at, "state": row.state, "providerMessageId": row.provider_message_id,
        "candidateId": None, "fileName": row.file_name, "contentType": row.content_type,
        "hasFile": bool(row.object_key), "fileUrl": f"/api/whatsapp/outbound/{row.id}/file" if row.object_key else None,
        "template": row.template, "errorCode": row.error_code, "lastError": row.last_error,
        "senderUserId": row.sender_user_id, "sentAt": row.sent_at, "deliveredAt": row.delivered_at, "readAt": row.read_at}


def _apply_event(attempt, event):
    stamp = event.occurred_at
    if event.status == "failed":
        attempt.failed_at = max(attempt.failed_at, stamp) if attempt.failed_at else stamp
        if attempt.state not in {"delivered", "read"}:
            attempt.state, attempt.error_code, attempt.last_error = "failed", event.error_code, event.error_detail or "WhatsApp could not deliver this message."
    else:
        field = {"sent": "sent_at", "delivered": "delivered_at", "read": "read_at"}[event.status]
        current = getattr(attempt, field)
        setattr(attempt, field, min(current, stamp) if current else stamp)
        rank = {"queued": 0, "preparing": 0, "sending": 0, "unknown": 0, "accepted": 1, "sent": 2, "failed": 3, "delivered": 4, "read": 5, "cancelled": 6}
        if rank[event.status] > rank.get(attempt.state, 0):
            attempt.state, attempt.last_error, attempt.error_code = event.status, None, None
    event.attempt_id = attempt.id


def reconcile_payload(db, payload, settings):
    """Only the signature-verified webhook calls this; disconnected channels still reconcile receipts."""
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            phone_id = str(value.get("metadata", {}).get("phone_number_id", ""))
            if phone_id != settings.whatsapp_phone_number_id: continue
            for status in value.get("statuses", []):
                if not isinstance(status, dict) or status.get("status") not in {"sent", "delivered", "read", "failed"}: continue
                identifier = str(status.get("id", ""))
                if not identifier or len(identifier) > 255: continue
                try:
                    number = peer_number(status.get("recipient_id"))
                    stamp = datetime.fromtimestamp(int(status["timestamp"]), timezone.utc)
                except (HTTPException, ValueError, KeyError, OverflowError, OSError): continue
                callback = str(status.get("biz_opaque_callback_data", ""))[:64] or None
                errors = status.get("errors") or []
                code = str(errors[0].get("code", ""))[:80] if errors and isinstance(errors[0], dict) else None
                key = hashlib.sha256(json.dumps([phone_id, identifier, number, status["status"], stamp.isoformat(), code]).encode()).hexdigest()
                eid = db.scalar(insert(WhatsAppDeliveryEvent).values(id=str(uuid4()), phone_number_id=phone_id,
                    provider_message_id=identifier, callback_data=callback, recipient_number=number, status=status["status"],
                    occurred_at=stamp, event_key=key, error_code=code,
                    error_detail=f"WhatsApp delivery failed (code {code or 'not supplied'})." if status["status"] == "failed" else None)
                    .on_conflict_do_nothing(index_elements=["event_key"]).returning(WhatsAppDeliveryEvent.id))
                if not eid:
                    eid = db.scalar(select(WhatsAppDeliveryEvent.id).where(WhatsAppDeliveryEvent.event_key == key))
                matches = [WhatsAppSendAttempt.provider_message_id == identifier]
                if callback: matches.append(WhatsAppSendAttempt.id == callback)
                attempts = list(db.scalars(select(WhatsAppSendAttempt).join(WhatsAppConversation).where(or_(*matches),
                    WhatsAppConversation.phone_number_id == phone_id, WhatsAppConversation.peer_number == number,
                    WhatsAppSendAttempt.state.in_(["sending", "unknown", "accepted", "sent", "delivered", "read", "failed"]))
                    .with_for_update(of=WhatsAppSendAttempt).limit(2)))
                if len(attempts) != 1: continue
                attempt = attempts[0]
                event = db.get(WhatsAppDeliveryEvent, eid)
                if event.attempt_id and event.attempt_id != attempt.id: continue
                if callback and callback != attempt.id: continue
                if attempt is None or (attempt.provider_message_id and attempt.provider_message_id != identifier): continue
                collision = db.scalar(select(WhatsAppSendAttempt.id).where(WhatsAppSendAttempt.provider_message_id == identifier, WhatsAppSendAttempt.id != attempt.id))
                if collision: continue
                attempt.provider_message_id = identifier
                _apply_event(attempt, event)


def _validate_dispatch(db, row, conversation, settings):
    _channel(db, conversation.phone_number_id, settings, sending=True)
    if row.sender_user_id:
        actor = db.scalar(select(User).where(User.id == row.sender_user_id).execution_options(populate_existing=True))
        permitted, reason = _send_permission(db, conversation, actor)
        if not permitted: raise HTTPException(403, reason)
    else:
        from app.services.intake_acknowledgements import configuration
        config = configuration(db, "whatsapp", conversation.phone_number_id)
        if not config or not config.enabled: raise HTTPException(409, "Automatic acknowledgements were disabled before dispatch.")
    if row.message_type != "template" and not window(conversation)[0]:
        raise HTTPException(409, "The reply window closed before sending. Use an approved template.")
    if row.message_type == "template":
        approved = _approved_template(db, conversation.phone_number_id, row.template)
        if row.body and rendered_template(approved, row.template.get("parameters", [])) != row.body:
            raise HTTPException(409, "The approved template changed after this message was queued. Review the updated template before creating a new send.")


def dispatch_attempt(attempt_id, *, sessions, settings, client=None):
    """Claim once, prepare media/templates, then durably mark sending before HTTP."""
    with sessions() as db:
        row = db.scalar(select(WhatsAppSendAttempt).where(WhatsAppSendAttempt.id == attempt_id).with_for_update(skip_locked=True))
        if row is None or row.state != "queued": return
        conversation = get_conversation(db, row.conversation_id)
        try: _validate_dispatch(db, row, conversation, settings)
        except HTTPException as exc:
            # Disconnect pauses work; other changed permissions/window need review.
            channel = db.get(WhatsAppChannel, conversation.phone_number_id)
            if (not channel or not channel.enabled or not settings.whatsapp_enabled or provider.missing_settings(settings)
                    or conversation.phone_number_id != settings.whatsapp_phone_number_id): return
            row.state, row.last_error = "cancelled", str(exc.detail); db.commit(); return
        claimed = utcnow()
        # Always include the lease timestamp in SQL, even if it equals the
        # existing value; ORM dirty tracking would otherwise substitute NOW().
        db.execute(update(WhatsAppSendAttempt).where(WhatsAppSendAttempt.id == row.id)
            .values(state="preparing", updated_at=claimed))
        object_key, filename, mime, content_hash = row.object_key, row.file_name, row.content_type, row.content_hash
        kind, template = row.message_type, row.template
        db.commit()
    try:
        media_id = None
        if object_key:
            content = read_bytes(settings, object_key)
            if not content_hash or hashlib.sha256(content).hexdigest() != content_hash:
                raise provider.WhatsAppError("The saved file looks different from the one queued to send. Nothing was sent.")
            media_id = provider.upload_media(settings, content, filename, mime, client=client)
        if kind == "template":
            with sessions() as db:
                sync_templates(db, settings, client=client, name=template["name"]); db.commit()
    except Exception:
        with sessions() as db:
            row = db.scalar(select(WhatsAppSendAttempt).where(WhatsAppSendAttempt.id == attempt_id).with_for_update())
            if row and row.state == "preparing" and row.updated_at == claimed:
                row.state, row.last_error = "failed", "We could not prepare the file or template. No message was sent."
                db.commit()
        return
    with sessions() as db:
        row = db.scalar(select(WhatsAppSendAttempt).where(WhatsAppSendAttempt.id == attempt_id).with_for_update())
        if row is None or row.state != "preparing" or row.updated_at != claimed: return
        conversation = get_conversation(db, row.conversation_id)
        try: _validate_dispatch(db, row, conversation, settings)
        except HTTPException as exc:
            channel = db.get(WhatsAppChannel, conversation.phone_number_id)
            paused = (not channel or not channel.enabled or not settings.whatsapp_enabled or provider.missing_settings(settings)
                or conversation.phone_number_id != settings.whatsapp_phone_number_id)
            row.state, row.last_error = "queued" if paused else "cancelled", str(exc.detail)
            db.commit(); return
        payload = {"messaging_product": "whatsapp", "recipient_type": "individual", "to": conversation.peer_number,
            "type": row.message_type, "biz_opaque_callback_data": row.id}
        if row.message_type == "text": payload["text"] = {"body": row.body, "preview_url": False}
        elif row.message_type == "template":
            payload["template"] = {"name": row.template["name"], "language": {"code": row.template["language"]}}
            parameters = row.template.get("parameters", [])
            if parameters: payload["template"]["components"] = [{"type": "body", "parameters": [{"type": "text", "text": value} for value in parameters]}]
        else:
            payload[row.message_type] = {"id": media_id}
            if row.body: payload[row.message_type]["caption"] = row.body
            if row.message_type == "document": payload["document"]["filename"] = row.file_name
        row.state, row.provider_media_id, row.updated_at = "sending", media_id, utcnow()
        db.commit()
    try:
        provider_id = provider.send_message(settings, payload, client=client)
        outcome, code, detail = "accepted", None, None
    except provider.SendRejected as exc:
        provider_id, outcome, code, detail = None, "failed", exc.code, str(exc)
    except Exception:
        provider_id, outcome, code, detail = None, "unknown", None, "The send outcome is unknown. Wait for signed delivery status; do not send the same message again."
    with sessions() as db:
        row = db.scalar(select(WhatsAppSendAttempt).where(WhatsAppSendAttempt.id == attempt_id).with_for_update())
        if row is None: return
        if provider_id and not row.provider_message_id:
            collision = db.scalar(select(WhatsAppSendAttempt.id).where(WhatsAppSendAttempt.provider_message_id == provider_id,
                WhatsAppSendAttempt.id != row.id))
            if collision:
                outcome, detail = "unknown", "The provider returned a conflicting receipt. Review delivery status; automatic resend is disabled."
            else: row.provider_message_id = provider_id
        if outcome == "accepted":
            row.accepted_at = utcnow()
            if row.state in {"sending", "unknown"}: row.state = outcome
        elif row.state in {"sending", "unknown"}:
            row.state, row.error_code, row.last_error = outcome, code, detail
            if outcome == "failed": row.failed_at = utcnow()
        if row.provider_message_id:
            conversation = get_conversation(db, row.conversation_id)
            for delivery in db.scalars(select(WhatsAppDeliveryEvent).where(WhatsAppDeliveryEvent.provider_message_id == row.provider_message_id,
                WhatsAppDeliveryEvent.phone_number_id == conversation.phone_number_id, WhatsAppDeliveryEvent.recipient_number == conversation.peer_number,
                or_(WhatsAppDeliveryEvent.callback_data.is_(None), WhatsAppDeliveryEvent.callback_data == row.id),
                or_(WhatsAppDeliveryEvent.attempt_id.is_(None), WhatsAppDeliveryEvent.attempt_id == row.id))
                .order_by(WhatsAppDeliveryEvent.occurred_at, WhatsAppDeliveryEvent.id)):
                _apply_event(row, delivery)
        db.commit()


def dispatch_queued(*, sessions, settings, limit=5, client=None):
    with sessions() as db:
        cutoff = utcnow() - timedelta(minutes=3)
        # A crashed preparation may upload another copy, but cannot duplicate a
        # recipient message. A crashed send is always uncertain and stays stopped.
        db.execute(update(WhatsAppSendAttempt).where(WhatsAppSendAttempt.state == "preparing", WhatsAppSendAttempt.updated_at < cutoff)
            .values(state="queued", updated_at=utcnow()))
        db.execute(update(WhatsAppSendAttempt).where(WhatsAppSendAttempt.state == "sending", WhatsAppSendAttempt.updated_at < cutoff)
            .values(state="unknown", last_error="Sending was interrupted. Wait for WhatsApp's delivery status before sending this message again.", updated_at=utcnow()))
        ids = list(db.scalars(select(WhatsAppSendAttempt.id).join(WhatsAppConversation).join(WhatsAppChannel)
            .where(WhatsAppSendAttempt.state == "queued", WhatsAppChannel.enabled.is_(True),
                WhatsAppChannel.phone_number_id == settings.whatsapp_phone_number_id)
            .order_by(WhatsAppSendAttempt.created_at, WhatsAppSendAttempt.id).limit(limit)))
        db.commit()
    for identifier in ids: dispatch_attempt(identifier, sessions=sessions, settings=settings, client=client)
    return len(ids)
