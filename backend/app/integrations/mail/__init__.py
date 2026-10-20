"""Read-only mail sources and transport-independent inbound message records."""

from .base import InboundAttachment, InboundMessage, MailAdapterError, MailMessageError

__all__ = ["InboundAttachment", "InboundMessage", "MailAdapterError", "MailMessageError"]
