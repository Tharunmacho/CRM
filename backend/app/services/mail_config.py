"""Resolve mailbox credentials from server configuration, never from public JSON."""
import json

from app.integrations.mail.imap import MailConfig


def configured_mailboxes(settings) -> dict[str, MailConfig]:
    entries = []
    if settings.email_accounts_json:
        try:
            entries = json.loads(settings.email_accounts_json)
            if not isinstance(entries, list):
                raise ValueError()
        except (ValueError, TypeError):
            raise ValueError("The list of mailboxes in the server settings is not valid. Ask your system administrator to fix it.") from None
    elif settings.imap_server and settings.imap_username and settings.imap_password:
        entries = [{
            "host": settings.imap_server, "port": settings.imap_port,
            "username": settings.imap_username, "password": settings.imap_password,
            "folder": settings.imap_folder, "use_ssl": settings.imap_use_ssl,
        }]
    accounts = {}
    try:
        for item in entries:
            address = item["username"].strip().lower()
            if not address or address in accounts or not item["password"]:
                raise ValueError()
            accounts[address] = MailConfig(
                host=item["host"], port=int(item.get("port", 993)),
                username=item["username"], password=item["password"],
                folder=item.get("folder", "INBOX"), use_ssl=item.get("use_ssl", True),
                smtp_host=item.get("smtp_host", getattr(settings, "smtp_server", None)),
                smtp_port=int(item.get("smtp_port", getattr(settings, "smtp_port", 587))),
                smtp_use_ssl=item.get("smtp_use_ssl", getattr(settings, "smtp_use_ssl", False)),
            )
    except (KeyError, ValueError, TypeError):
        raise ValueError("Each mailbox needs its own email address, mail server name, port and password. Ask your system administrator to fix the server settings.") from None
    return accounts
