"""Admin-approved automatic receipt acknowledgements and visible recovery state."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator
from sqlalchemy import select

from app.core.config import get_settings
from app.core.deps import current_user, require_role
from app.db.models import AuditEntry, IntakeAcknowledgement, IntakeAcknowledgementSettings, Role, WhatsAppSendAttempt
from app.db.session import get_db
from app.services.intake_acknowledgements import (DEFAULT_BODY, attempt_out, channel, completed_source,
    configuration, connected, email_suppression, utcnow)

router = APIRouter(prefix="/api/intake-acknowledgements", tags=["Intake acknowledgements"])
admin = require_role(Role.admin)
SourceKind = Literal["email", "whatsapp"]


def snapshot(db, source_kind, channel_id):
    account = channel(db, source_kind, channel_id)
    config = configuration(db, source_kind, channel_id)
    rows = list(db.scalars(select(IntakeAcknowledgement).where(IntakeAcknowledgement.source_kind == source_kind,
        IntakeAcknowledgement.channel_id == channel_id).order_by(IntakeAcknowledgement.created_at.desc(), IntakeAcknowledgement.id.desc()).limit(20)))
    settings = get_settings()
    return {"sourceKind": source_kind, "channelId": channel_id, "enabled": bool(config and config.enabled),
        "body": config.body if config else DEFAULT_BODY, "enabledAt": config.enabled_at if config else None,
        "connected": connected(source_kind, account),
        "workerEnabled": settings.email_ingestion_enabled if source_kind == "email" else settings.whatsapp_enabled,
        "attempts": [attempt_out(db, row) for row in rows]}


@router.get("/{source_kind}/{channel_id}")
def read_settings(source_kind: SourceKind, channel_id: str, db=Depends(get_db), user=Depends(current_user)):
    return snapshot(db, source_kind, channel_id)


class SettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    enabled: StrictBool
    body: str = Field(min_length=10, max_length=2000)
    previewedBody: str | None = None

    @field_validator("body")
    @classmethod
    def plain_text(cls, value):
        if "\x00" in value:
            raise ValueError("The acknowledgement cannot contain null characters.")
        return value


@router.patch("/{source_kind}/{channel_id}")
def change_settings(source_kind: SourceKind, channel_id: str, body: SettingsIn,
                    db=Depends(get_db), user=Depends(admin)):
    account = channel(db, source_kind, channel_id, lock=True)
    config = configuration(db, source_kind, channel_id)
    if body.enabled:
        if not connected(source_kind, account):
            raise HTTPException(409, "Connect this mailbox or WhatsApp number before you turn on automatic replies.")
        if (config is None or not config.enabled or config.body != body.body) and body.previewedBody != body.body:
            raise HTTPException(422, "Preview this reply and confirm the exact text before you turn it on.")
    changed_text = bool(config and config.body != body.body)
    was_enabled = bool(config and config.enabled)
    if config is None:
        config = IntakeAcknowledgementSettings(source_kind=source_kind, channel_id=channel_id, body=body.body)
        db.add(config)
    config.enabled, config.body, config.updated_by = body.enabled, body.body, user.id
    if body.enabled and not was_enabled:
        config.enabled_at = utcnow()
    # Stop unsent old copies when an administrator disables/changes the text.
    # In-flight sends retain their result/unknown state and are never reset.
    if not body.enabled or changed_text:
        queued = list(db.scalars(select(IntakeAcknowledgement).where(IntakeAcknowledgement.source_kind == source_kind,
            IntakeAcknowledgement.channel_id == channel_id, IntakeAcknowledgement.status.in_(["pending", "paused"]))
            .with_for_update()))
        for row in queued:
            if row.outbound_id:
                outgoing = db.scalar(select(WhatsAppSendAttempt).where(WhatsAppSendAttempt.id == row.outbound_id).with_for_update())
                if outgoing and outgoing.state != "queued":
                    continue
                if outgoing:
                    outgoing.state, outgoing.last_error = "cancelled", "Automatic acknowledgement settings changed before dispatch."
            row.status, row.last_error = "suppressed", "Automatic acknowledgement settings changed before dispatch."
    db.flush()
    db.add(AuditEntry(actor_id=user.id, action="Updated intake acknowledgement settings", entity_type="intake_ack_settings",
        entity_id=config.id, meta={"sourceKind": source_kind, "channelId": channel_id, "enabled": body.enabled, "textChanged": changed_text}))
    db.commit()
    return snapshot(db, source_kind, channel_id)


@router.post("/attempts/{attempt_id}/retry")
def retry(attempt_id: str, db=Depends(get_db), user=Depends(admin)):
    identity = db.get(IntakeAcknowledgement, attempt_id)
    if identity is None:
        raise HTTPException(404, "Acknowledgement not found.")
    account = channel(db, identity.source_kind, identity.channel_id, lock=True)
    row = db.scalar(select(IntakeAcknowledgement).where(IntakeAcknowledgement.id == attempt_id)
        .with_for_update().execution_options(populate_existing=True))
    if row.source_kind != "email":
        raise HTTPException(409, "Check this reply in the sent messages of the WhatsApp conversation.")
    if row.status == "pending":
        return attempt_out(db, row)  # Replay of a successful retry request.
    if row.status not in {"failed", "paused"}:
        raise HTTPException(409, "Only a reply that failed or was paused can be sent again. If you are not sure it was delivered, check with the email provider first.")
    config = configuration(db, row.source_kind, row.channel_id)
    if not connected(row.source_kind, account) or not config or not config.enabled:
        raise HTTPException(409, "Reconnect the channel and turn on automatic replies before you try again.")
    if row.body != config.body:
        raise HTTPException(409, "The approved message has changed, so this older reply cannot be sent again. Check the original message and send a new reply if needed.")
    source = completed_source(db, "email", row.source_id)
    reason = email_suppression(db, source, account) if source else "The source intake is not fully saved."
    if reason:
        raise HTTPException(409, reason)
    row.status, row.last_error = "pending", None
    db.add(AuditEntry(actor_id=user.id, action="Retried intake acknowledgement", entity_type="intake_acknowledgement", entity_id=row.id))
    db.commit()
    return attempt_out(db, row)


class ResolveIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    outcome: Literal["sent", "stop"]
    providerChecked: StrictBool
    reason: str = Field(min_length=8, max_length=1000)


@router.post("/attempts/{attempt_id}/resolve")
def resolve(attempt_id: str, body: ResolveIn, db=Depends(get_db), user=Depends(admin)):
    row = db.scalar(select(IntakeAcknowledgement).where(IntakeAcknowledgement.id == attempt_id).with_for_update())
    if row is None:
        raise HTTPException(404, "Acknowledgement not found.")
    if row.source_kind != "email" or attempt_out(db, row)["status"] != "unknown" or not body.providerChecked:
        raise HTTPException(409, "Check with the email provider first. You can only settle a reply here when it is not clear whether it was sent.")
    row.status = "sent" if body.outcome == "sent" else "suppressed"
    row.last_error = None if body.outcome == "sent" else "Stopped after administrator reviewed the provider; no resend was queued."
    if body.outcome == "sent":
        row.sent_at = utcnow()
    db.add(AuditEntry(actor_id=user.id, action="Resolved uncertain intake acknowledgement", entity_type="intake_acknowledgement",
        entity_id=row.id, meta={"outcome": body.outcome, "providerChecked": True, "reason": body.reason}))
    db.commit()
    return attempt_out(db, row)
