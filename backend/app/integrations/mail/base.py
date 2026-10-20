"""Provider-neutral data passed from mailbox readers to durable ingestion."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime


class MailAdapterError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class MailMessageError(MailAdapterError):
    def __init__(self, code: str, message: str, *, uid: int, provider_id: str, retryable: bool = False):
        super().__init__(code, message, retryable=retryable)
        self.uid = uid
        self.provider_id = provider_id


@dataclass(frozen=True)
class InboundAttachment:
    provider_attachment_id: str
    filename: str
    mime: str
    data: bytes = field(repr=False)
    content_hash: str


@dataclass(frozen=True)
class InboundMessage:
    provider_id: str
    subject: str = field(repr=False)
    from_name: str = field(repr=False)
    from_address: str = field(repr=False)
    body: str = field(repr=False)
    received_at: datetime
    unread: bool
    attachments: tuple[InboundAttachment, ...]
    uid: int | None = None
    uid_validity: str | None = None
    folder: str | None = None
    warnings: tuple[str, ...] = ()

    def source_key(self, account_id: str) -> str:
        """Provider identifiers are scoped by the immutable CRM account ID."""
        value = json.dumps([account_id, self.provider_id], separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(value.encode("utf-8")).hexdigest()
