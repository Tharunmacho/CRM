"""Password hashing and session tokens.

Argon2id is the current recommendation for password storage; the parameters below are
the argon2-cffi defaults, which target roughly 50ms per verification on server hardware.
Sessions are opaque random tokens stored in the database rather than JWTs, so an admin
can genuinely force a logout — a signed token cannot be withdrawn before it expires.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

_hasher = PasswordHasher()

SESSION_TTL = timedelta(days=14)
SESSION_COOKIE = "crm_session"


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, hashed: str | None) -> bool:
    if not hashed:
        return False
    try:
        _hasher.verify(hashed, password)
        return True
    except (VerifyMismatchError, InvalidHashError):
        return False


def needs_rehash(hashed: str) -> bool:
    """True when the stored hash used weaker parameters than the current defaults."""
    try:
        return _hasher.check_needs_rehash(hashed)
    except InvalidHashError:
        return True


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def session_expiry() -> datetime:
    return datetime.now(timezone.utc) + SESSION_TTL
