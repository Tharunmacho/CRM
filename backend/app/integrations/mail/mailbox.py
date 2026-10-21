"""Bounded IMAP mailbox actions with UID-specific mutations and safe failures."""
import base64
import imaplib
import re
from dataclasses import replace
from datetime import datetime, timezone

from .imap import IMAPMailbox, MailAdapterError, _mailbox_wire_name


class MailMutationError(MailAdapterError):
    def __init__(self, message, *, unknown=False):
        super().__init__("remote_unknown" if unknown else "remote_failed", message)
        self.unknown = unknown


def decode_folder(value):
    def decode(match):
        raw = match.group(1)
        if not raw:
            return "&"
        return base64.b64decode(raw.replace(",", "/") + "=" * (-len(raw) % 4)).decode("utf-16-be")
    return re.sub(r"&([^-]*)-", decode, value)


def role_for(name, flags=()):
    special = {"\\inbox": "inbox", "\\sent": "sent", "\\drafts": "drafts", "\\trash": "trash", "\\archive": "archive", "\\junk": "spam"}
    for flag in flags:
        if flag.lower() in special:
            return special[flag.lower()]
    return {"inbox": "inbox", "sent": "sent", "sent mail": "sent", "sent items": "sent",
        "drafts": "drafts", "trash": "trash", "deleted items": "trash", "archive": "archive",
        "spam": "spam", "junk": "spam"}.get(name.casefold(), "custom")


class WritableMailbox(IMAPMailbox):
    @staticmethod
    def _uid(uid):
        if type(uid) is not int or uid <= 0:
            raise MailMutationError("The message identifier is invalid. Refresh the mailbox.")

    def select_folder(self, folder, expected=None, *, writable=False):
        self.config = replace(self.config, folder=folder)
        cursor = self._selected(expected)
        if writable:
            try:
                status, _ = self._connection.select(_mailbox_wire_name(folder), readonly=False)
                if status != "OK":
                    raise MailMutationError("Zoho did not allow changes to this folder.")
                _, values = self._connection.response("UIDVALIDITY")
                if str(int(values[0])) != cursor.uid_validity:
                    raise MailMutationError("The folder identifiers changed. Refresh the mailbox before changing this message.")
            except MailAdapterError:
                raise
            except Exception:
                raise MailMutationError("The folder could not be opened for changes. Refresh and try again.") from None
        return cursor

    def folders(self):
        self.connect()
        try:
            status, rows = self._connection.list()
            if status != "OK":
                raise ValueError()
            result = []
            for row in rows or []:
                if isinstance(row, tuple):
                    prefix, name = row
                    text = prefix.decode("ascii", "replace")
                    name = name.decode("ascii")
                else:
                    text = row.decode("ascii")
                    match = re.match(r'^\(([^)]*)\)\s+(?:"[^"\\]*(?:\\.[^"\\]*)*"|NIL)\s+(.+)$', text)
                    if not match:
                        continue
                    name = match.group(2)
                    if name.startswith('"') and name.endswith('"'):
                        name = re.sub(r"\\(.)", r"\1", name[1:-1])
                flags = re.match(r"^\(([^)]*)\)", text).group(1).split()
                if "\\noselect" in [f.lower() for f in flags]:
                    continue
                name = decode_folder(name)
                total = unread = None
                status, counts = self._connection.status(_mailbox_wire_name(name), "(MESSAGES UNSEEN)")
                if status == "OK" and counts:
                    count_text = b" ".join(r for r in counts if isinstance(r, bytes))
                    total_match, unread_match = re.search(rb"MESSAGES\s+(\d+)", count_text), re.search(rb"UNSEEN\s+(\d+)", count_text)
                    total = int(total_match.group(1)) if total_match else None
                    unread = int(unread_match.group(1)) if unread_match else None
                result.append({"name": name, "role": role_for(name, flags), "total": total, "unread": unread})
            return result
        except MailAdapterError:
            raise
        except Exception:
            raise MailAdapterError("folders_failed", "Zoho folders could not be listed. Check the connection and IMAP folder visibility settings.", retryable=True) from None

    def page(self, folder, *, search="", offset=0, limit=25, unread=None):
        cursor = self.select_folder(folder)
        if any(c in search for c in "\r\n\x00"):
            raise MailAdapterError("search_invalid", "Search cannot contain control characters.")
        try:
            flags = [] if unread is None else ["UNSEEN" if unread else "SEEN"]
            if search:
                # UTF-8 SEARCH literals permit non-ASCII names without command injection.
                self._connection.literal = search.encode("utf-8")
                status, rows = self._connection.uid("SEARCH", "CHARSET", "UTF-8", "UNDELETED", *flags, "TEXT")
            else:
                status, rows = self._connection.uid("SEARCH", None, "UNDELETED", *flags)
            if status != "OK":
                raise ValueError()
            uids = sorted({int(v) for v in ((rows or [b""])[0] or b"").split()}, reverse=True)
            return cursor, len(uids), [self.fetch(uid, uid_validity=cursor.uid_validity) for uid in uids[offset:offset + limit]]
        except MailAdapterError:
            raise
        except Exception:
            raise MailAdapterError("search_failed", "Zoho could not search this folder. Refresh or try a shorter search.", retryable=True) from None

    def find_message_id(self, folder, message_id):
        cursor = self.select_folder(folder)
        if not re.fullmatch(r"<[^<>\s]+>", message_id):
            raise MailAdapterError("message_id", "This message has no stable remote identifier.")
        try:
            status, data = self._connection.uid("SEARCH", None, "UNDELETED", "HEADER", "Message-ID", '"' + message_id.replace('"', '') + '"')
            if status != "OK":
                raise ValueError()
            return cursor, [int(value) for value in ((data or [b""])[0] or b"").split()]
        except Exception:
            raise MailAdapterError("search_failed", "The provider could not confirm the message state.", retryable=True) from None

    def set_flags(self, folder, uid, validity, *, unread=None, starred=None):
        self._uid(uid)
        self.select_folder(folder, validity, writable=True)
        issued = False
        try:
            for flag, enabled in (("\\Seen", None if unread is None else not unread), ("\\Flagged", starred)):
                if enabled is not None:
                    issued = True
                    status, _ = self._connection.uid("STORE", str(uid), "+FLAGS.SILENT" if enabled else "-FLAGS.SILENT", f"({flag})")
                    if status != "OK":
                        raise MailMutationError("Zoho refused this flag change. Refresh to check the current flags.", unknown=True)
            return self.fetch(uid, uid_validity=validity)
        except MailAdapterError:
            raise
        except Exception:
            raise MailMutationError("The flag change could not be confirmed. Refresh the message before trying again.", unknown=issued) from None

    def move(self, folder, uid, validity, destination):
        self._uid(uid)
        self.select_folder(folder, validity, writable=True)
        caps = {c.decode().upper() if isinstance(c, bytes) else str(c).upper() for c in self._connection.capabilities}
        if "MOVE" not in caps and "UIDPLUS" not in caps:
            raise MailMutationError("This Zoho IMAP server does not support moving one message safely. Enable MOVE or UIDPLUS support with the mailbox administrator.")
        copied = False
        try:
            status, _ = self._connection.uid("MOVE" if "MOVE" in caps else "COPY", str(uid), _mailbox_wire_name(destination))
            if status != "OK":
                raise MailMutationError("Zoho refused the move. Refresh before trying again.")
            _, rows = self._connection.response("COPYUID")
            match = re.search(rb"(\d+)\s+\d+\s+(\d+)", b" ".join(v for v in rows or [] if isinstance(v, bytes)))
            if "MOVE" not in caps:
                copied = True
                # Only this UID is removed. A failure leaves a durable unknown move
                # for explicit reconciliation; the service never repeats COPY.
                status, _ = self._connection.uid("STORE", str(uid), "+FLAGS.SILENT", "(\\Deleted)")
                if status != "OK":
                    raise MailMutationError("The destination copy exists but removing the source was not confirmed. Reconcile both folders.", unknown=True)
                status, _ = self._connection.uid("EXPUNGE", str(uid))
                if status != "OK":
                    raise MailMutationError("The destination copy exists but source removal is unconfirmed. Reconcile both folders.", unknown=True)
            return (match.group(1).decode(), int(match.group(2))) if match else None
        except MailMutationError:
            raise
        except Exception:
            raise MailMutationError("The move outcome is unknown. Refresh the source and destination folders before retrying.", unknown=True) from None

    def create_folder(self, name):
        if not name.strip() or len(name) > 255 or any(char in name for char in "\r\n\x00"):
            raise MailAdapterError("folder_name", "Enter a valid folder name.")
        self.connect()
        try:
            status, _ = self._connection.create(_mailbox_wire_name(name))
            if status != "OK":
                raise MailMutationError("Zoho refused to create this folder. Refresh the folders before retrying.")
        except MailAdapterError:
            raise
        except Exception:
            raise MailMutationError("Folder creation could not be confirmed. Refresh the folder list.", unknown=True) from None

    def append_draft(self, folder, raw):
        self.connect()
        try:
            status, _ = self._connection.append(_mailbox_wire_name(folder), "(\\Draft \\Seen)", imaplib.Time2Internaldate(datetime.now(timezone.utc)), raw)
            if status != "OK":
                raise MailMutationError("Zoho refused to save the draft.")
            _, values = self._connection.response("APPENDUID")
            match = re.search(rb"(\d+)\s+(\d+)", b" ".join(v for v in values or [] if isinstance(v, bytes)))
            return (match.group(1).decode(), int(match.group(2))) if match else None
        except MailMutationError:
            raise
        except Exception:
            raise MailMutationError("The Zoho draft save could not be confirmed. The CRM draft is safe; check its sync state before retrying.", unknown=True) from None

    def remove_draft(self, folder, uid, validity):
        self._uid(uid)
        self.select_folder(folder, validity, writable=True)
        caps = {c.decode().upper() if isinstance(c, bytes) else str(c).upper() for c in self._connection.capabilities}
        if "UIDPLUS" not in caps:
            raise MailMutationError("The provider cannot remove one draft safely. Remove the old draft in Zoho.")
        try:
            status, _ = self._connection.uid("STORE", str(uid), "+FLAGS.SILENT", "(\\Deleted)")
            if status != "OK":
                raise MailMutationError("Zoho refused to remove this draft.")
            status, _ = self._connection.uid("EXPUNGE", str(uid))
            if status != "OK":
                raise MailMutationError("Draft removal could not be confirmed.", unknown=True)
        except MailAdapterError:
            raise
        except Exception:
            raise MailMutationError("Draft removal could not be confirmed. Refresh before retrying.", unknown=True) from None
