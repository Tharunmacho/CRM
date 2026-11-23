"""WhatsApp numbers to ignore: the admin's list of senders the CRM never works on.

Staff, agents, clients and spammers share the business number with candidates. A file from a number on this list is kept as an "ignored"
receipt (visible in the WhatsApp inbox, never read, never a candidate) and the number gets no automatic acknowledgement. Staff can still
write to such a number by hand: this list only governs what arrives.

Numbers are compared the way the rest of the CRM compares them (`normalize_phone`: digits only, the agency's calling code optional), so
"+91 98765 43210", "919876543210", "09876543210" and "9876543210" are one number. Nothing is cached between requests; the list is short.
"""
from __future__ import annotations

import re

from sqlalchemy import select

from app.db.models import WhatsAppIgnoredNumber
from app.services.candidate_identity import normalize_phone

IGNORED_REASON = "Sender is on the ignore list"
IGNORED_ACK_REASON = "This number is on the WhatsApp ignore list; automatic acknowledgement skipped."
_TYPED = re.compile(r"^\+?[\d\s().\-]+$")


def key_of(value) -> str:
    """The comparison key of a typed or provider number ('' when it is not a number we can use: 7 to 15 digits, digits and the usual separators only)."""
    raw = str(value or "").strip()
    if not raw or not _TYPED.match(raw):
        return ""
    key = normalize_phone(raw)
    return key if 7 <= len(key) <= 15 else ""


def ignored_keys(db) -> frozenset[str]:
    return frozenset(db.scalars(select(WhatsAppIgnoredNumber.phone_key)))


def is_ignored(db, number, keys: frozenset[str] | None = None) -> bool:
    key = key_of(number)
    return bool(key) and key in (ignored_keys(db) if keys is None else keys)
