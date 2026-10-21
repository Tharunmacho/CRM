"""Zoho connection policy. Credentials are accepted once and encrypted at rest."""
import re
from dataclasses import replace

from app.integrations.mail.imap import MailConfig, MailAdapterError

REGIONS = ("com", "in", "eu", "com.au", "jp", "ca", "com.cn", "sa")


def validate_config(config):
    incoming = re.fullmatch(r"imap(pro)?\.zoho\.(com|in|eu|com\.au|jp|ca|com\.cn|sa)", config.host.casefold())
    outgoing = re.fullmatch(r"smtp(pro)?\.zoho\.(com|in|eu|com\.au|jp|ca|com\.cn|sa)", (config.smtp_host or "").casefold())
    if not incoming or not outgoing or incoming.groups() != outgoing.groups():
        raise MailAdapterError("zoho_server", "Use the matching Zoho IMAP and SMTP server names shown in your account's Server Configuration Details.")
    if config.port != 993 or not config.use_ssl or (config.smtp_port, config.smtp_use_ssl) not in {(465, True), (587, False)}:
        raise MailAdapterError("zoho_tls", "Zoho requires IMAP 993 with SSL and SMTP 465 with SSL or 587 with STARTTLS.")
    return config


def make_config(address, password, *, region="com", account_type="organization", imap_host=None, smtp_host=None, smtp_port=465):
    if region not in REGIONS or account_type not in {"personal", "organization"}:
        raise MailAdapterError("zoho_region", "Select the data center and account type shown in Zoho's Server Configuration Details.")
    suffix = "pro" if account_type == "organization" else ""
    return validate_config(MailConfig(host=imap_host or f"imap{suffix}.zoho.{region}", port=993,
        username=address, password=password, smtp_host=smtp_host or f"smtp{suffix}.zoho.{region}",
        smtp_port=smtp_port, smtp_use_ssl=smtp_port == 465))


def account_config(db, settings, account):
    from app.services.mail_oauth import decrypt
    from app.services.mail_config import configured_mailboxes
    if account.disconnected_at:
        raise MailAdapterError("disconnected", "This mailbox is disconnected. An administrator can reconnect it.")
    if account.provider == "zoho":
        saved = decrypt(settings, (account.secrets or {}).get("encrypted", ""))
        return make_config(account.address, saved.get("password", ""), region=saved.get("region", "com"),
            account_type=saved.get("account_type", "organization"), imap_host=saved.get("imap_host"),
            smtp_host=saved.get("smtp_host"), smtp_port=saved.get("smtp_port", 465))
    if account.provider == "imap":
        config = configured_mailboxes(settings).get(account.address)
        if config:
            return validate_config(config)
    raise MailAdapterError("zoho_only", "Only Zoho Mail connections are enabled. Existing message history is retained.")


def readiness(settings):
    from app.services.mail_oauth import cipher
    try:
        cipher(settings)
        return [{"provider": "zoho", "ready": True, "message": "Connect using the Zoho server details and app password for your account."}]
    except MailAdapterError:
        return [{"provider": "zoho", "ready": False, "message": "Configure MAIL_TOKEN_ENCRYPTION_KEY on the CRM server before connecting Zoho Mail."}]
