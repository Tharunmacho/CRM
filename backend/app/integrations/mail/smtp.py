"""Explicit user replies only. Never retry an ambiguous SMTP acceptance."""
import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import format_datetime, parseaddr
from datetime import datetime, timezone
from email import policy

from .base import MailAdapterError


class MailSendError(MailAdapterError):
    def __init__(self, message, *, unknown=False):
        super().__init__("send_unknown" if unknown else "send_failed", message)
        self.unknown = unknown


def address(value):
    parsed = parseaddr(value or "")[1]
    if not re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", parsed) or any(char in (value or "") for char in "\r\n\x00"):
        raise MailSendError("This message has no valid reply address.")
    return parsed


def send_reply(config, message, body, reply_id):
    if not config.smtp_host:
        raise MailSendError("Outgoing mail is not configured for this mailbox. Configure SMTP_SERVER or smtp_host for this account.")
    recipient = address(message.reply_to or message.from_address)
    outgoing = EmailMessage()
    outgoing["From"], outgoing["To"] = address(config.username), recipient
    subject = re.sub(r"[\r\n\x00]", " ", message.subject or "")[:990]
    outgoing["Subject"] = subject if subject.lower().startswith("re:") else f"Re: {subject}"
    outgoing["Message-ID"] = f"<{reply_id}@crm-reply.local>"
    outgoing["Date"] = format_datetime(datetime.now(timezone.utc))
    message_ids = re.findall(r"<[^<>\s]+>", message.message_id_header or "")
    if message_ids:
        outgoing["In-Reply-To"] = message_ids[0]
        references = re.findall(r"<[^<>\s]+>", message.references_header or "")[-20:]
        outgoing["References"] = " ".join(dict.fromkeys([*references, message_ids[0]]))
    outgoing.set_content(body)
    deliver(config, outgoing, [recipient])
    return str(outgoing["Message-ID"])


def compose(config, *, message_id, to, cc, bcc, subject, body, attachments=(), in_reply_to=None, references=None):
    outgoing = EmailMessage(policy=policy.SMTP)
    outgoing["From"] = address(config.username)
    for header, values in (("To", to), ("Cc", cc)):
        if values:
            outgoing[header] = ", ".join(address(value) for value in values)
    if any(char in subject for char in "\r\n\x00"):
        raise MailSendError("The subject cannot contain line breaks.")
    outgoing["Subject"], outgoing["Message-ID"] = subject, message_id
    outgoing["Date"] = format_datetime(datetime.now(timezone.utc))
    if in_reply_to:
        ids = re.findall(r"<[^<>\s]+>", in_reply_to)
        if ids:
            outgoing["In-Reply-To"] = ids[0]
            outgoing["References"] = " ".join(dict.fromkeys([*re.findall(r"<[^<>\s]+>", references or "")[-20:], ids[0]]))
    outgoing.set_content(body)
    for attachment in attachments:
        mime = attachment.get("mime", "application/octet-stream")
        if not re.fullmatch(r"[a-zA-Z0-9!#$&^_.+-]+/[a-zA-Z0-9!#$&^_.+-]+", mime):
            mime = "application/octet-stream"
        major, minor = mime.split("/", 1)
        outgoing.add_attachment(attachment["data"], maintype=major, subtype=minor, filename=attachment["name"])
    return outgoing, list(dict.fromkeys(address(value) for value in [*to, *cc, *bcc]))


def deliver(config, outgoing, recipients):
    if not config.smtp_host:
        raise MailSendError("Outgoing mail is not configured for this mailbox.")
    server = None
    sending = False
    try:
        context = ssl.create_default_context()
        if config.smtp_use_ssl:
            server = smtplib.SMTP_SSL(config.smtp_host, config.smtp_port, timeout=20, context=context)
        else:
            server = smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=20)
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
        if config.access_token:
            auth = f"user={config.username}\x01auth=Bearer {config.access_token}\x01\x01"
            server.auth("XOAUTH2", lambda challenge=None: auth if challenge is None else "", initial_response_ok=True)
        else:
            server.login(config.username, config.password)
        sending = True
        refused = server.send_message(outgoing, from_addr=config.username, to_addrs=recipients)
        return list(refused)
    except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused, smtplib.SMTPDataError, smtplib.SMTPAuthenticationError, smtplib.SMTPNotSupportedError):
        raise MailSendError("The mailbox provider refused this reply. Check the recipient and outgoing-mail permissions before trying again.") from None
    except Exception:
        raise MailSendError("Delivery could not be confirmed. Check the mailbox's Sent folder before composing another reply." if sending else "A secure outgoing-mail connection could not be established. Check this mailbox's SMTP configuration.", unknown=sending) from None
    finally:
        if server is not None:
            try:
                server.close()
            except Exception:
                pass
