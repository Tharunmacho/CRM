"""Bounded, read-only IMAP intake. This module cannot send, move or flag mail."""
from __future__ import annotations

import hashlib
import imaplib
import json
import math
import re
import ssl
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime

from .base import InboundMessage, MailAdapterError, MailMessageError
from .mime import parse_message


@dataclass(frozen=True)
class MailConfig:
    host: str
    port: int
    username: str = field(repr=False)
    password: str = field(repr=False)
    folder: str = "INBOX"
    use_ssl: bool = True
    timeout: float = 30
    max_message_bytes: int = 30 * 1024 * 1024

    def __post_init__(self):
        if not self.host or re.search(r"[\s/@\\]", self.host):
            raise MailAdapterError("invalid_config", "Configure a valid IMAP hostname without a URL prefix.")
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise MailAdapterError("invalid_config", "Configure a valid IMAP port.")
        if not self.username or not self.password:
            raise MailAdapterError("invalid_config", "Configure an IMAP username and password or app password.")
        if not self.folder or any(c in self.folder + self.username for c in "\r\n\x00"):
            raise MailAdapterError("invalid_config", "The IMAP mailbox name or username is invalid.")
        if not math.isfinite(self.timeout) or self.timeout <= 0:
            raise MailAdapterError("invalid_config", "The IMAP timeout must be a finite positive number.")
        if type(self.max_message_bytes) is not int or self.max_message_bytes <= 0:
            raise MailAdapterError("invalid_config", "The email size limit must be a positive byte count.")


@dataclass(frozen=True)
class MailboxCursor:
    uid_validity: str
    last_uid: int


def mailbox_source_id(config: MailConfig) -> str:
    """Bind a cursor to its source without storing passwords or connection strings."""
    source = {
        "host": config.host.casefold(), "port": config.port,
        "username": config.username, "folder": config.folder, "use_ssl": config.use_ssl,
    }
    encoded = json.dumps(source, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def require_cursor_source(cursor: dict, config: MailConfig) -> None:
    if not cursor.get("source_id"):
        raise MailAdapterError(
            "source_unbound",
            "This mailbox cursor has no verified source. Re-register the mailbox before syncing.",
        )
    if cursor["source_id"] != mailbox_source_id(config):
        raise MailAdapterError(
            "source_changed",
            "Mailbox connection settings changed. Restore the original server, username and folder before syncing.",
        )


def provider_message_id(folder: str, uid_validity: str, uid: int) -> str:
    # Folder strings may exceed the DB's provider ID column; hash only the folder.
    folder_key = hashlib.sha256(folder.encode("utf-8")).hexdigest()
    return f"imap:{folder_key}:{uid_validity}:{uid}"


def _mailbox_wire_name(folder: str) -> str:
    # IMAP modified UTF-7, including folders with spaces/ampersands.
    result: list[str] = []
    pending: list[str] = []
    def flush():
        if pending:
            import base64
            encoded = base64.b64encode("".join(pending).encode("utf-16-be")).decode().rstrip("=").replace("/", ",")
            result.append("&" + encoded + "-")
            pending.clear()
    for char in folder:
        if 0x20 <= ord(char) <= 0x7e:
            flush()
            result.append("&-" if char == "&" else char)
        else:
            pending.append(char)
    flush()
    value = "".join(result).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{value}"'


class IMAPMailbox:
    def __init__(self, config: MailConfig):
        self.config = config
        self._connection = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *_exc):
        self.close()

    def connect(self) -> None:
        if self._connection is not None:
            return
        context = ssl.create_default_context()
        try:
            if self.config.use_ssl:
                connection = imaplib.IMAP4_SSL(self.config.host, self.config.port,
                                              ssl_context=context, timeout=self.config.timeout)
                self._connection = connection
            else:
                connection = imaplib.IMAP4(self.config.host, self.config.port, timeout=self.config.timeout)
                self._connection = connection
                # Credentials must never be sent over cleartext IMAP.
                status, _ = connection.starttls(ssl_context=context)
                if status != "OK":
                    raise MailAdapterError("tls_required", "The IMAP server must support TLS.")
        except MailAdapterError:
            self.close()
            raise
        except Exception:
            self.close()
            raise MailAdapterError("connection_failed", "Could not establish a secure IMAP connection.", retryable=True) from None
        try:
            status, _ = connection.login(self.config.username, self.config.password)
            if status != "OK":
                raise imaplib.IMAP4.error("login refused")
        except Exception:
            self.close()
            raise MailAdapterError("authentication_failed", "IMAP sign-in failed. Check the account credentials or app password.") from None

    def close(self) -> None:
        connection, self._connection = self._connection, None
        if connection is not None:
            try:
                connection.logout()
            except Exception:
                try:
                    connection.shutdown()
                except Exception:
                    pass
        # Never use CLOSE/EXPUNGE: those commands can modify the mailbox.

    def _selected(self, expected: str | None = None) -> MailboxCursor:
        self.connect()
        try:
            status, _ = self._connection.select(_mailbox_wire_name(self.config.folder), readonly=True)
            if status != "OK":
                raise MailAdapterError("mailbox_unavailable", "The configured IMAP folder cannot be opened read-only.")
            _, values = self._connection.response("UIDVALIDITY")
            validity = str(int(values[0]))
            if int(validity) <= 0:
                raise ValueError
            if expected is not None and validity != str(expected):
                raise MailAdapterError("uidvalidity_changed", "The mailbox UIDVALIDITY changed. Review and reconnect the source before resuming.")
            _, next_values = self._connection.response("UIDNEXT")
            if not next_values or next_values[0] is None:
                status, rows = self._connection.uid("SEARCH", None, "ALL")
                if status != "OK":
                    raise ValueError
                last = max([int(value) for value in (rows[0] or b"").split()], default=0)
            else:
                last = int(next_values[0]) - 1
                if last < 0:
                    raise ValueError
            return MailboxCursor(validity, last)
        except MailAdapterError:
            raise
        except Exception:
            raise MailAdapterError("mailbox_metadata_failed", "Could not read the mailbox's stable identifiers.", retryable=True) from None

    def probe(self) -> MailboxCursor:
        """Current high-water mark; first connection can start with future mail."""
        return self._selected()

    def list_uids(self, *, after_uid: int, uid_validity: str, limit: int = 25) -> list[int]:
        if type(after_uid) is not int or after_uid < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise MailAdapterError("invalid_cursor", "Use a nonnegative UID cursor and a batch size between 1 and 100.")
        self._selected(uid_validity)
        try:
            status, data = self._connection.uid("SEARCH", None, "UID", f"{after_uid + 1}:*")
            if status != "OK":
                raise ValueError
            # n:* can include an older UID when n exceeds the highest UID.
            values = [int(uid) for uid in ((data or [b""])[0] or b"").split()]
            return sorted({uid for uid in values if uid > after_uid})[:limit]
        except Exception:
            raise MailAdapterError("search_failed", "Could not list new email messages.", retryable=True) from None

    def fetch(self, uid: int, *, uid_validity: str) -> InboundMessage:
        if type(uid) is not int or uid <= 0:
            raise MailAdapterError("invalid_cursor", "The message UID must be a positive integer.")
        self._selected(uid_validity)
        provider_id = provider_message_id(self.config.folder, str(uid_validity), uid)
        def failure(code: str, message: str, retryable=False):
            return MailMessageError(code, message, uid=uid, provider_id=provider_id, retryable=retryable)
        try:
            status, size_rows = self._connection.uid("FETCH", str(uid), "(UID RFC822.SIZE)")
            if status != "OK":
                raise failure("message_unavailable", "The source email could not be fetched.", True)
            metadata = next((row for row in size_rows or [] if isinstance(row, bytes)
                             and re.search(rb"\bUID\s+" + str(uid).encode() + rb"\b", row)), b"")
            size_match = re.search(rb"\bRFC822\.SIZE\s+(\d+)", metadata)
            if not size_match:
                raise failure("message_unavailable", "The source email is missing or has no valid size.")
            size = int(size_match.group(1))
            if size > self.config.max_message_bytes:
                raise failure("message_too_large", "The email exceeds the configured message size limit.")
            request = f"(UID BODY.PEEK[]<0.{self.config.max_message_bytes + 1}> INTERNALDATE FLAGS)"
            status, body_rows = self._connection.uid("FETCH", str(uid), request)
            if status != "OK":
                raise failure("fetch_failed", "The source email body could not be read.", True)
            matches = [row for row in body_rows or [] if isinstance(row, tuple) and len(row) == 2
                       and isinstance(row[0], bytes) and isinstance(row[1], bytes)
                       and re.search(rb"\bUID\s+" + str(uid).encode() + rb"\b", row[0])]
            if len(matches) != 1:
                raise failure("fetch_failed", "The server did not return the requested email.", True)
            details, raw = matches[0]
            if len(raw) > self.config.max_message_bytes:
                raise failure("message_too_large", "The fetched email exceeds the configured message size limit.")
            if len(raw) != size:
                raise failure("incomplete_message", "The email download was incomplete. Retry the source message.", True)
            received = None
            date_match = re.search(rb'INTERNALDATE "([^"]+)"', details)
            if date_match:
                try:
                    received = parsedate_to_datetime(date_match.group(1).decode("ascii").replace("-", " ", 2))
                except (ValueError, TypeError, UnicodeError):
                    pass
            unread = b"\\seen" not in {flag.lower() for flag in imaplib.ParseFlags(details)}
            return parse_message(raw, provider_id=provider_id, uid=uid, uid_validity=str(uid_validity),
                                 folder=self.config.folder, received_at=received, unread=unread)
        except MailMessageError:
            raise
        except Exception:
            raise failure("fetch_failed", "The email could not be downloaded safely. Retry the source message.", True) from None
