"""Meta Cloud API authentication and bounded media retrieval. No outbound messages."""
import base64
import hashlib
import hmac
import re
from urllib.parse import urlparse

import httpx

MAX_MEDIA_BYTES = 25 * 1024 * 1024
SUPPORTED_TYPES = {"application/pdf", "image/jpeg", "image/png", "image/webp", "application/msword", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}


class WhatsAppError(ValueError):
    pass


def missing_settings(settings):
    names = ("whatsapp_phone_number_id", "whatsapp_graph_version", "whatsapp_access_token", "whatsapp_app_secret", "whatsapp_verify_token")
    missing = [name.upper() for name in names if not getattr(settings, name, None)]
    if settings.whatsapp_graph_version and not re.fullmatch(r"v[0-9]+\.[0-9]+", settings.whatsapp_graph_version):
        missing.append("WHATSAPP_GRAPH_VERSION (format vN.N)")
    return missing


def verify_signature(body, header, secret):
    if not secret or not header or not re.fullmatch(r"sha256=[0-9a-f]{64}", header):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(header, expected)


def download_media(settings, media_id, *, client=None):
    if not re.fullmatch(r"[0-9]+", media_id or ""):
        raise WhatsAppError("Invalid provider media ID")
    own_client = client is None
    client = client or httpx.Client(timeout=30, follow_redirects=False)
    headers = {"Authorization": f"Bearer {settings.whatsapp_access_token}"}
    try:
        metadata = client.get(f"https://graph.facebook.com/{settings.whatsapp_graph_version}/{media_id}",
                              params={"phone_number_id": settings.whatsapp_phone_number_id}, headers=headers)
        metadata.raise_for_status()
        info = metadata.json()
        parsed = urlparse(info.get("url", ""))
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or parsed.port not in (None, 443) or parsed.username or parsed.password or not (host == "lookaside.fbsbx.com" or host.endswith(".fbcdn.net")):
            raise WhatsAppError("The provider returned an unsupported media address")
        mime = info.get("mime_type", "").split(";", 1)[0].lower()
        if mime not in SUPPORTED_TYPES:
            raise WhatsAppError("This document format is not supported for candidate intake")
        if int(info.get("file_size") or 0) > MAX_MEDIA_BYTES:
            raise WhatsAppError("This attachment exceeds the CRM's 25 MB limit")
        content = bytearray()
        with client.stream("GET", info["url"], headers=headers) as response:
            response.raise_for_status()
            for block in response.iter_bytes():
                content.extend(block)
                if len(content) > MAX_MEDIA_BYTES:
                    raise WhatsAppError("This attachment exceeds the CRM's 25 MB limit")
        if not content:
            raise WhatsAppError("The provider returned an empty attachment")
        digest = hashlib.sha256(content).digest()
        expected = info.get("sha256")
        if expected and expected not in {digest.hex(), base64.b64encode(digest).decode()}:
            raise WhatsAppError("The attachment checksum does not match the provider receipt")
        return bytes(content), mime
    except (httpx.HTTPError, TypeError, KeyError) as exc:
        raise WhatsAppError("WhatsApp media could not be retrieved. Check the server credentials and retry.") from exc
    finally:
        if own_client:
            client.close()
