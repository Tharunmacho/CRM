"""Parse inbound MIME without relying on filenames to identify documents."""
from __future__ import annotations

import hashlib
import io
import re
import zipfile
from datetime import datetime, timezone
from email import policy
from email.message import Message
from email.parser import BytesParser
from email.utils import getaddresses, parseaddr, parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import PurePosixPath, PureWindowsPath

from .base import InboundAttachment, InboundMessage, MailMessageError


_DECLARED_DOCUMENTS = {
    "application/pdf", "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def document_mime(data: bytes) -> str | None:
    if b"%PDF-" in data[:1024]:
        return "application/pdf"
    if data.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return "application/msword"
    if data.startswith(b"PK"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                if "word/document.xml" in archive.namelist():
                    return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        except zipfile.BadZipFile:
            pass
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith((b"II*\x00", b"MM\x00*")):
        return "image/tiff"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data.startswith(b"BM"):
        return "image/bmp"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _parts(message: Message, path: str = "1"):
    # An attached .eml is its own attachment, never the outer message's body.
    if message.get_content_type() == "message/rfc822":
        yield path, message
    elif message.is_multipart():
        for index, part in enumerate(message.iter_parts(), 1):
            yield from _parts(part, f"{path}.{index}")
    else:
        yield path, message


def _safe_filename(filename: str, path: str, mime: str) -> str:
    suffix = {
        "application/pdf": ".pdf", "application/msword": ".doc",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
        "image/jpeg": ".jpg", "image/png": ".png", "image/tiff": ".tiff",
        "image/gif": ".gif", "image/bmp": ".bmp", "image/webp": ".webp",
    }.get(mime, ".bin")
    name = PurePosixPath(PureWindowsPath(filename or "").name).name
    name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip()
    if not name or name in {".", ".."}:
        name = f"attachment-{path}{suffix}"
    return name[:255]


class _TextHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        elif tag in {"p", "div", "br", "li", "tr"} and not self.hidden:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def parse_message(
    data: bytes, *, provider_id: str, uid: int, uid_validity: str, folder: str,
    received_at: datetime | None, unread: bool, starred: bool = False,
) -> InboundMessage:
    def malformed():
        return MailMessageError("malformed_message", "The email has damaged MIME content. Review the source message.",
                                uid=uid, provider_id=provider_id)
    try:
        message = BytesParser(policy=policy.default).parsebytes(data)
        if message.defects:
            raise malformed()
        plain: list[str] = []
        html: list[str] = []
        attachments: list[InboundAttachment] = []
        warnings: list[str] = []
        for path, part in _parts(message):
            declared = part.get_content_type()
            filename = str(part.get_filename() or "")
            disposition = part.get_content_disposition()
            payload = part.get_payload(decode=True)
            if payload is None and declared == "message/rfc822":
                nested = part.get_payload()
                payload = b"\n".join(item.as_bytes(policy=policy.default) for item in nested) if isinstance(nested, list) else b""
            payload = payload or b""
            if part.defects:
                raise malformed()
            detected = document_mime(payload)
            attached = (
                disposition == "attachment" or bool(filename) or bool(detected)
                or declared in _DECLARED_DOCUMENTS or declared.startswith("image/")
                or declared == "message/rfc822"
            )
            if declared in {"text/plain", "text/html"} and not filename and disposition != "attachment":
                attached = False
            if attached:
                # Retain even empty/unsupported explicit attachments: ingestion
                # records their failure instead of silently losing the evidence.
                mime = detected or "application/octet-stream"
                attachments.append(InboundAttachment(
                    provider_attachment_id=f"{provider_id}:part:{path}",
                    filename=_safe_filename(filename, path, mime), mime=mime,
                    data=payload, content_hash=hashlib.sha256(payload).hexdigest(),
                ))
                continue
            if declared in {"text/plain", "text/html"}:
                charset = part.get_content_charset() or "utf-8"
                try:
                    text = payload.decode(charset, errors="replace")
                except LookupError:
                    text = payload.decode("utf-8", errors="replace")
                    warnings.append("An unrecognized email character set was decoded as UTF-8.")
                (plain if declared == "text/plain" else html).append(text)
        body = "\n\n".join(plain)
        if not plain and html:
            converter = _TextHTML()
            converter.feed("\n".join(html))
            body = "".join(converter.parts).strip()
        if len(body) > 100_000:
            body = body[:100_000]
            warnings.append("Email body preview was limited to 100,000 characters; attachment contents are unchanged.")
        if received_at is None:
            try:
                received_at = parsedate_to_datetime(str(message.get("Date", "")))
            except (TypeError, ValueError, OverflowError):
                received_at = datetime.now(timezone.utc)
                warnings.append("The message had no valid received date; the retrieval time was recorded.")
        if received_at.tzinfo is None:
            received_at = received_at.replace(tzinfo=timezone.utc)
        from_name, from_address = parseaddr(str(message.get("From", "")))
        return InboundMessage(
            provider_id=provider_id, uid=uid, uid_validity=uid_validity, folder=folder,
            subject=str(message.get("Subject", "")), from_name=from_name,
            from_address=from_address, body=body, received_at=received_at.astimezone(timezone.utc),
            unread=unread, attachments=tuple(attachments), warnings=tuple(dict.fromkeys(warnings)),
            message_id_header=str(message.get("Message-ID", ""))[:2000] or None,
            references_header=str(message.get("References", ""))[:8000] or None,
            reply_to=parseaddr(str(message.get("Reply-To", "")))[1][:255] or None,
            to_addresses=tuple(a[:255] for _, a in getaddresses(message.get_all("To", [])) if a),
            cc_addresses=tuple(a[:255] for _, a in getaddresses(message.get_all("Cc", [])) if a), starred=starred,
            bcc_addresses=tuple(a[:255] for _, a in getaddresses(message.get_all("Bcc", [])) if a),
        )
    except MailMessageError:
        raise
    except Exception:
        raise malformed() from None
