"""Provider registry for connected mailboxes.

Any number of accounts may be connected, each through one of four sources:

* ``gmail`` and ``microsoft`` use OAuth; tokens are encrypted server-side.
* ``zoho`` uses an app password with the Zoho server allowlist.
* ``imap`` is any other IMAP/SMTP mailbox (including the legacy server-configured
  accounts). Credentials entered in the app are encrypted; connections always use TLS.

``account_config`` is the single place that turns a stored account into a live
connection configuration, so receiving, reading, replying and drafting all share it.
"""
from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import dataclass

from app.integrations.mail.imap import MailAdapterError, MailConfig
from app.services import mail_oauth, zoho_mail
from app.services.mail_config import configured_mailboxes

LIVE_PROVIDERS = frozenset({"zoho", "imap", "gmail", "microsoft"})
OAUTH_PROVIDERS = frozenset({"gmail", "microsoft"})
LABELS = {"gmail": "Gmail", "microsoft": "Microsoft 365", "zoho": "Zoho Mail", "imap": "IMAP / SMTP"}

IMAP_PORTS = {993: True, 143: False}  # port -> implicit TLS; 143 must upgrade with STARTTLS
SMTP_PORTS = {465: True, 587: False}
_HOSTNAME = re.compile(r"(?=.{1,253}$)([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}")


@dataclass(frozen=True)
class CustomServer:
    imap_host: str
    imap_port: int
    smtp_host: str
    smtp_port: int


def is_live(provider: str) -> bool:
    return provider in LIVE_PROVIDERS


def assert_public_host(host: str) -> None:
    """Refuse hosts that resolve to loopback, private or link-local addresses.

    Administrators enter these hosts, but the server connects to them; this keeps a
    mailbox form from being used to probe the hosting network or cloud metadata.
    """
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        addresses = [literal]
    else:
        if not _HOSTNAME.fullmatch(host):
            raise MailAdapterError("invalid_host", "Enter a mail server name such as imap.example.com.")
        try:
            addresses = [ipaddress.ip_address(item[4][0]) for item in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)]
        except (socket.gaierror, ValueError):
            raise MailAdapterError("invalid_host", f"We could not find the mail server {host}. Check the spelling and try again.") from None
    if not addresses or any(not address.is_global for address in addresses):
        raise MailAdapterError("invalid_host", "We cannot connect to mail servers on private or local networks. Use your mail provider's public server name.")


def make_custom_config(address: str, password: str, server: CustomServer, *, verify_host: bool = True) -> MailConfig:
    imap_host, smtp_host = server.imap_host.strip().casefold(), server.smtp_host.strip().casefold()
    if server.imap_port not in IMAP_PORTS or server.smtp_port not in SMTP_PORTS:
        raise MailAdapterError("invalid_port", "Use port 993 or 143 for receiving email, and port 465 or 587 for sending. Your mail provider can tell you which to use.")
    if not password or len(password) > 1000 or any(char in password for char in "\r\n\x00"):
        raise MailAdapterError("invalid_password", "Enter a valid mailbox password or app password.")
    if verify_host:
        for host in {imap_host, smtp_host}:
            assert_public_host(host)
    return MailConfig(host=imap_host, port=server.imap_port, username=address, password=password,
        use_ssl=IMAP_PORTS[server.imap_port], smtp_host=smtp_host, smtp_port=server.smtp_port,
        smtp_use_ssl=SMTP_PORTS[server.smtp_port])


def validate_config(config: MailConfig) -> MailConfig:
    """Policy for server-configured (legacy) IMAP accounts."""
    if config.port not in IMAP_PORTS or IMAP_PORTS[config.port] != config.use_ssl:
        raise MailAdapterError("invalid_port", "Receiving email must use port 993 or 143. Ask an administrator to fix the mailbox settings.")
    if config.smtp_host and (config.smtp_port, config.smtp_use_ssl) not in set(SMTP_PORTS.items()):
        raise MailAdapterError("invalid_port", "Sending email must use port 465 or 587. Ask an administrator to fix the mailbox settings.")
    return config


def account_config(db, settings, account) -> MailConfig:
    if account.disconnected_at:
        raise MailAdapterError("disconnected", "This mailbox is disconnected. An administrator can reconnect it.")
    if account.provider in OAUTH_PROVIDERS:
        return mail_oauth.account_config(db, settings, account)
    if account.provider == "zoho":
        return zoho_mail.zoho_config(settings, account)
    if account.provider == "imap":
        envelope = (account.secrets or {}).get("encrypted")
        if envelope:
            saved = mail_oauth.decrypt(settings, envelope)
            return make_custom_config(account.address, saved.get("password", ""), CustomServer(
                saved["imap_host"], int(saved["imap_port"]), saved["smtp_host"], int(saved["smtp_port"])))
        config = configured_mailboxes(settings).get(account.address)
        if config is None:
            raise MailAdapterError("mail_configuration", "The sign-in details for this mailbox are missing. An administrator can reconnect it.")
        return validate_config(config)
    raise MailAdapterError("provider_archived", "This mail provider is no longer supported. Stored history remains readable.")


def readiness(settings) -> list[dict]:
    """Which connection methods this server is configured to offer."""
    result = [{"provider": item["provider"], "label": LABELS[item["provider"]], "ready": item["ready"], "message": item["message"]}
        for item in mail_oauth.readiness(settings)]
    try:
        mail_oauth.cipher(settings)
        ready, message = True, "Connect with an app password and the mail server details."
    except MailAdapterError:
        ready, message = False, "Mailbox connections are not set up on this server yet. Ask your system administrator to finish the setup, then try again."
    result += [{"provider": "zoho", "label": LABELS["zoho"], "ready": ready, "message": message},
        {"provider": "imap", "label": LABELS["imap"], "ready": ready, "message": message}]
    return result
