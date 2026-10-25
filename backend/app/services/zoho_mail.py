"""Zoho connection policy. Credentials are accepted once and encrypted at rest."""
import re
from app.integrations.mail.imap import MailConfig, MailAdapterError

REGIONS = ("com", "in", "eu", "com.au", "jp", "ca", "com.cn", "sa")


def validate_config(config):
    incoming = re.fullmatch(r"imap(pro)?\.zoho\.(com|in|eu|com\.au|jp|ca|com\.cn|sa)", config.host.casefold())
    outgoing = re.fullmatch(r"smtp(pro)?\.zoho\.(com|in|eu|com\.au|jp|ca|com\.cn|sa)", (config.smtp_host or "").casefold())
    if not incoming or not outgoing or incoming.groups() != outgoing.groups():
        raise MailAdapterError("zoho_server", "Use the Zoho server names for receiving and sending email shown in your Zoho account settings. Both must be for the same Zoho region.")
    if config.port != 993 or not config.use_ssl or (config.smtp_port, config.smtp_use_ssl) not in {(465, True), (587, False)}:
        raise MailAdapterError("zoho_tls", "Zoho needs port 993 for receiving email, and port 465 or 587 for sending.")
    return config


def make_config(address, password, *, region="com", account_type="organization", imap_host=None, smtp_host=None, smtp_port=465):
    if region not in REGIONS or account_type not in {"personal", "organization"}:
        raise MailAdapterError("zoho_region", "Choose the Zoho region and account type shown in your Zoho account settings.")
    suffix = "pro" if account_type == "organization" else ""
    return validate_config(MailConfig(host=imap_host or f"imap{suffix}.zoho.{region}", port=993,
        username=address, password=password, smtp_host=smtp_host or f"smtp{suffix}.zoho.{region}",
        smtp_port=smtp_port, smtp_use_ssl=smtp_port == 465))


def zoho_config(settings, account):
    from app.services.mail_oauth import decrypt
    saved = decrypt(settings, (account.secrets or {}).get("encrypted", ""))
    return make_config(account.address, saved.get("password", ""), region=saved.get("region", "com"),
        account_type=saved.get("account_type", "organization"), imap_host=saved.get("imap_host"),
        smtp_host=saved.get("smtp_host"), smtp_port=saved.get("smtp_port", 465))


def account_config(db, settings, account):
    """Resolve any connected provider; kept here for callers written for Zoho-only mail."""
    from app.services import mail_providers
    return mail_providers.account_config(db, settings, account)


def readiness(settings):
    from app.services.mail_oauth import cipher
    try:
        cipher(settings)
        return [{"provider": "zoho", "ready": True, "message": "Connect using the Zoho server details and app password for your account."}]
    except MailAdapterError:
        return [{"provider": "zoho", "ready": False, "message": "Zoho Mail connections are not set up on this server yet. Ask your system administrator to finish the setup, then try again."}]
