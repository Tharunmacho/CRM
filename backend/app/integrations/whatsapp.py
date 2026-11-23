"""Meta Cloud API authentication, bounded media and explicit outbound requests."""
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

class WhatsAppReviewError(WhatsAppError):
    """A problem a person must look at (bad file type, too big, damaged or needs a check), so the message goes to review and is not retried."""



class TemplatesNotConfigured(WhatsAppError):
    """The WhatsApp Business Account ID is not set, so approved templates can be neither loaded nor sent."""


BUSINESS_ACCOUNT_MISSING = ("WHATSAPP_BUSINESS_ACCOUNT_ID is not set on this server, so approved templates cannot be loaded or sent. "
                            "Ask your administrator to add it.")

GENERIC_REJECTION = "WhatsApp did not accept the message. Fix the problem, then send it again."
TOO_FAST = "WhatsApp is receiving messages too fast. Wait a few minutes, then send it again."

# What an administrator reads for the Meta error codes that matter in practice. Only these fixed words are ever shown: the provider's
# own title, message and details are never stored (they can quote the recipient or the request).
ERROR_MESSAGES = {
    "131047": "The 24-hour reply window has closed. Send an approved template instead.",
    "131030": "This number is not on the allowed list of the test WhatsApp number. Add it in Meta, or use the live number.",
    "132018": "A template value has line breaks, tabs or four spaces in a row. Remove them and send again.",
    "190": "The WhatsApp access token has expired or is not valid. Tell your administrator to create a new one.",
    "133010": "The business WhatsApp number is not registered. Tell your administrator to register it in Meta.",
    "131026": "WhatsApp could not deliver to this number. Check that it is correct and uses WhatsApp.",
    "130429": TOO_FAST, "80007": TOO_FAST, "131056": TOO_FAST, "429": TOO_FAST,
}
TEMPLATE_PROBLEM = "The template is not approved, is paused or is missing. Refresh the templates, then choose an approved one."
# Nothing is sent when Meta answers with these (or when the connection is never made), so one more try later is safe.
RETRYABLE_CODES = {"130429", "80007", "131056", "429", "connect_failed"}


def error_message(code):
    """Plain words for a Meta error code (the generic sentence when the code is unknown)."""
    text = str(code or "").strip()
    if text in ERROR_MESSAGES:
        return ERROR_MESSAGES[text]
    if text.isdigit() and 132000 <= int(text) <= 132016:
        return TEMPLATE_PROBLEM
    return GENERIC_REJECTION


def describe_error(code):
    """`error_message` plus the code, for the history a person reads and for support."""
    text = str(code or "").strip()
    message = error_message(text)
    return f"{message} (WhatsApp code {text})" if text.isdigit() and message != GENERIC_REJECTION else message


class SendRejected(WhatsAppError):
    """WhatsApp refused the message, or it never left this server. Nothing was sent; `retryable` says whether trying again later can work."""

    def __init__(self, code, message=None, retryable=None):
        super().__init__(message or describe_error(code))
        self.code = str(code)
        self.retryable = (self.code in RETRYABLE_CODES) if retryable is None else bool(retryable)


class SendUnknown(WhatsAppError):
    """Request may have reached Meta. Never automatically send it again."""


CHAT_TYPES = {
    "image/jpeg": ("image", 5 * 1024 * 1024), "image/png": ("image", 5 * 1024 * 1024),
    "application/pdf": ("document", MAX_MEDIA_BYTES), "text/plain": ("document", MAX_MEDIA_BYTES),
    "application/msword": ("document", MAX_MEDIA_BYTES),
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ("document", MAX_MEDIA_BYTES),
    "application/vnd.ms-excel": ("document", MAX_MEDIA_BYTES),
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ("document", MAX_MEDIA_BYTES),
    "application/vnd.ms-powerpoint": ("document", MAX_MEDIA_BYTES),
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ("document", MAX_MEDIA_BYTES),
    "audio/aac": ("audio", 16 * 1024 * 1024), "audio/mp4": ("audio", 16 * 1024 * 1024),
    "audio/mpeg": ("audio", 16 * 1024 * 1024), "audio/amr": ("audio", 16 * 1024 * 1024),
    "audio/ogg": ("audio", 16 * 1024 * 1024), "video/mp4": ("video", 16 * 1024 * 1024),
    "video/3gpp": ("video", 16 * 1024 * 1024),
}


GRAPH_VERSION = re.compile(r"v[0-9]+\.[0-9]+")
NOT_SET_UP = "WhatsApp is not set up correctly on this server. Ask your system administrator to check the WhatsApp settings."


def valid_graph_version(settings):
    return bool(GRAPH_VERSION.fullmatch(getattr(settings, "whatsapp_graph_version", None) or ""))


def graph_url(settings, identifier, edge):
    if not re.fullmatch(r"[0-9]+", str(identifier or "")) or not valid_graph_version(settings):
        raise WhatsAppError(NOT_SET_UP)
    return f"https://graph.facebook.com/{settings.whatsapp_graph_version}/{identifier}/{edge}"


def send_message(settings, payload, *, client=None):
    # A missing or malformed setting is checked before anything can leave: it is "not sent" (fix the settings, send again), never "unknown".
    try:
        url = graph_url(settings, settings.whatsapp_phone_number_id, "messages")
    except WhatsAppError:
        raise SendRejected("not_configured", NOT_SET_UP) from None
    own = client is None
    client = client or httpx.Client(timeout=30, follow_redirects=False)
    try:
        try:
            response = client.post(url,
                headers={"Authorization": f"Bearer {settings.whatsapp_access_token}"}, json=payload)
        except (httpx.ConnectError, httpx.ConnectTimeout):
            # The connection was never made, so no byte of the message left this server: it is "not sent", and safe to try again.
            raise SendRejected("connect_failed", "We could not reach WhatsApp. Nothing was sent.", retryable=True) from None
        if 400 <= response.status_code < 500:
            try: code = response.json().get("error", {}).get("code", response.status_code)
            except ValueError: code = response.status_code
            raise SendRejected(code)
        if response.status_code >= 300:
            raise SendUnknown("We could not tell if WhatsApp sent this message. Wait for its delivery status before sending it again.")
        message_id = response.json().get("messages", [{}])[0].get("id")
        if not isinstance(message_id, str) or not message_id or len(message_id) > 255:
            raise SendUnknown("WhatsApp did not confirm this message. Wait for its delivery status before sending it again.")
        return message_id
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
        if isinstance(exc, (SendRejected, SendUnknown)):
            raise
        raise SendUnknown("The connection dropped before WhatsApp confirmed the message. It may have been sent, so wait for its delivery status.") from None
    finally:
        if own: client.close()


def upload_media(settings, content, filename, content_type, *, client=None):
    mime = content_type.split(";", 1)[0].lower()
    if mime not in CHAT_TYPES or not content or len(content) > CHAT_TYPES[mime][1]:
        raise WhatsAppError("WhatsApp does not accept this type or size of file.")
    own = client is None
    client = client or httpx.Client(timeout=45, follow_redirects=False)
    try:
        response = client.post(graph_url(settings, settings.whatsapp_phone_number_id, "media"),
            headers={"Authorization": f"Bearer {settings.whatsapp_access_token}"},
            data={"messaging_product": "whatsapp", "type": mime}, files={"file": (filename, content, content_type)})
        response.raise_for_status()
        identifier = response.json().get("id")
        if not re.fullmatch(r"[0-9]{1,100}", str(identifier or "")):
            raise WhatsAppError("WhatsApp did not confirm the file upload. Try again.")
        return str(identifier)
    except (httpx.HTTPError, ValueError, TypeError):
        raise WhatsAppError("WhatsApp could not prepare the attachment. No message was sent.") from None
    finally:
        if own: client.close()


def fetch_templates(settings, *, client=None, name=None):
    """Bounded trusted-origin paging. Never follow provider-supplied next URLs."""
    account = getattr(settings, "whatsapp_business_account_id", None)
    if not account:
        raise TemplatesNotConfigured(BUSINESS_ACCOUNT_MISSING)
    url = graph_url(settings, account, "message_templates")
    own = client is None
    client = client or httpx.Client(timeout=30, follow_redirects=False)
    output, after = [], None
    try:
        for _ in range(20):
            params = {"fields": "id,name,language,status,category,components", "limit": 100}
            if after: params["after"] = after
            if name: params["name"] = name
            response = client.get(url, params=params, headers={"Authorization": f"Bearer {settings.whatsapp_access_token}"})
            response.raise_for_status()
            payload = response.json()
            rows = payload.get("data")
            if not isinstance(rows, list): raise WhatsAppError("WhatsApp sent back an answer we could not read. Try again.")
            output.extend(rows)
            paging = payload.get("paging") or {}
            if not paging.get("next"): return output
            cursor = (paging.get("cursors") or {}).get("after")
            if not isinstance(cursor, str) or not cursor or cursor == after or len(cursor) > 2000:
                raise WhatsAppError("WhatsApp sent back an answer we could not read. Try again.")
            after = cursor
        raise WhatsAppError("There are too many WhatsApp templates to load (over 2,000). Ask your administrator to remove the ones you do not use.")
    except (httpx.HTTPError, ValueError, TypeError, KeyError):
        raise WhatsAppError("We could not load your approved WhatsApp templates. Check the WhatsApp settings, then try again.") from None
    finally:
        if own: client.close()


def missing_settings(settings):
    names = ("whatsapp_phone_number_id", "whatsapp_graph_version", "whatsapp_access_token", "whatsapp_app_secret", "whatsapp_verify_token")
    missing = [name.upper() for name in names if not getattr(settings, name, None)]
    version = getattr(settings, "whatsapp_graph_version", None)
    if version and not GRAPH_VERSION.fullmatch(version):
        missing.append("WHATSAPP_GRAPH_VERSION (format vN.N)")
    return missing


def verify_signature(body, header, secret):
    if not secret or not header or not re.fullmatch(r"sha256=[0-9a-f]{64}", header):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(header, expected)


def download_media(settings, media_id, *, client=None, chat=False):
    if not re.fullmatch(r"[0-9]+", media_id or ""):
        raise WhatsAppError("WhatsApp sent a file we could not identify.")
    if not valid_graph_version(settings):
        raise WhatsAppError(NOT_SET_UP)
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
            raise WhatsAppError("WhatsApp sent a file link we cannot use.")
        mime = info.get("mime_type", "").split(";", 1)[0].lower()
        if mime not in (SUPPORTED_TYPES | CHAT_TYPES.keys() if chat else SUPPORTED_TYPES):
            raise WhatsAppReviewError("We cannot use this type of file for candidates. Send a PDF, Word document or image.")
        maximum = CHAT_TYPES.get(mime, (None, MAX_MEDIA_BYTES))[1] if chat else MAX_MEDIA_BYTES
        if int(info.get("file_size") or 0) > maximum:
            raise WhatsAppReviewError("This file is too big. The limit is 25 MB.")
        content = bytearray()
        with client.stream("GET", info["url"], headers=headers) as response:
            response.raise_for_status()
            for block in response.iter_bytes():
                content.extend(block)
                if len(content) > maximum:
                    raise WhatsAppReviewError("This file is too big. The limit is 25 MB.")
        if not content:
            raise WhatsAppError("WhatsApp sent an empty file.")
        digest = hashlib.sha256(content).digest()
        expected = info.get("sha256")
        if expected and expected not in {digest.hex(), base64.b64encode(digest).decode()}:
            raise WhatsAppReviewError("The file did not download properly. Try again.")
        return bytes(content), mime
    except (httpx.HTTPError, TypeError, KeyError) as exc:
        raise WhatsAppError("We could not download the file from WhatsApp. Try again, and contact your administrator if it keeps happening.") from exc
    finally:
        if own_client:
            client.close()
