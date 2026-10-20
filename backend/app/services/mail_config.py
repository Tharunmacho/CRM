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
            raise ValueError("EMAIL_ACCOUNTS_JSON must be an array of IMAP account objects.") from None
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
            )
    except (KeyError, ValueError, TypeError):
        raise ValueError("Each mailbox needs a unique username, host, port and app password.") from None
    return accounts
