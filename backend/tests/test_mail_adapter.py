"""Synthetic IMAP/MIME contracts: these tests never open a socket."""
from __future__ import annotations

import io
import ssl
import zipfile
from dataclasses import replace
from datetime import datetime, timezone
from email.message import EmailMessage

import pytest

from app.integrations.mail import imap as subject
from app.integrations.mail.base import MailAdapterError, MailMessageError
from app.integrations.mail.mime import document_mime, parse_message


@pytest.mark.parametrize("changed", [
    {"host": "another.example.test"}, {"port": 143}, {"username": "another@example.test"},
    {"folder": "Applications"}, {"use_ssl": False},
])
def test_cursor_source_identity_changes_with_mailbox_endpoint_or_folder(changed):
    original = subject.MailConfig("imap.example.test", 993, "recruitment@example.test", "synthetic-secret")
    cursor = {"uid_validity": "same-validity", "last_uid": 1000,
              "source_id": subject.mailbox_source_id(original)}
    with pytest.raises(MailAdapterError, match="connection settings changed"):
        subject.require_cursor_source(cursor, replace(original, **changed))


def test_cursor_source_identity_excludes_password_and_tuning_settings():
    original = subject.MailConfig("imap.example.test", 993, "recruitment@example.test", "synthetic-secret")
    changed = replace(original, password="rotated-synthetic-secret", timeout=60, max_message_bytes=1024)
    source_id = subject.mailbox_source_id(original)
    assert source_id == subject.mailbox_source_id(changed)
    assert len(source_id) == 64 and "synthetic" not in source_id
    subject.require_cursor_source({"source_id": source_id}, changed)


def message_bytes(*, attachment=True, seen=False):
    message = EmailMessage()
    message["From"] = "Example Applicant <applicant@example.test>"
    message["To"] = "recruitment@example.test"
    message["Subject"] = "Synthetic application"
    message["Date"] = "Mon, 19 Oct 2026 11:00:00 +0530"
    message.set_content("Please consider the attached document.")
    if attachment:
        message.add_attachment(b"%PDF-1.7\nsynthetic-content", maintype="application", subtype="octet-stream",
                               filename="misnamed-photo.jpg")
    return message.as_bytes()


class FakeIMAP:
    def __init__(self, raw=None):
        self.raw = raw if raw is not None else message_bytes()
        self.calls = []
        self.validity = b"123"
        self.next_uid = b"11"
        self.search = b"8 7 10 9"
        self.size = len(self.raw)
        self.fetch_uid = 9
        self.flags = b"()"
        self.login_error = None
        self.tls_error = None
        self.status = "OK"

    def login(self, username, password):
        self.calls.append(("LOGIN", username, password))
        if self.login_error:
            raise self.login_error
        return "OK", [b"authenticated"]

    def starttls(self, *, ssl_context):
        self.calls.append(("STARTTLS",))
        assert ssl_context.verify_mode == ssl.CERT_REQUIRED
        if self.tls_error:
            raise self.tls_error
        return "OK", [b"ready"]

    def select(self, folder, *, readonly):
        self.calls.append(("SELECT", folder, readonly))
        assert readonly is True
        return self.status, [b"10"]

    def response(self, name):
        return name, [self.validity if name == "UIDVALIDITY" else self.next_uid]

    def uid(self, command, *args):
        self.calls.append((command,) + args)
        if command == "SEARCH":
            return "OK", [self.search]
        assert command == "FETCH"
        assert args[0] == "9"
        if args[1] == "(UID RFC822.SIZE)":
            return "OK", [f"4 (UID 9 RFC822.SIZE {self.size})".encode()]
        assert "BODY.PEEK[]" in args[1]
        assert "RFC822)" not in args[1]
        metadata = (
            f'4 (UID {self.fetch_uid} INTERNALDATE "20-Oct-2026 12:30:00 +0530" FLAGS '
        ).encode() + self.flags + f" BODY[]<0> {{{len(self.raw)}}}".encode()
        return "OK", [(metadata, self.raw), b")"]

    def logout(self):
        self.calls.append(("LOGOUT",))
        return "BYE", []

    def shutdown(self):
        self.calls.append(("SHUTDOWN",))


@pytest.fixture
def connection(monkeypatch):
    fake = FakeIMAP()
    def secure(host, port, *, ssl_context, timeout):
        assert host == "imap.example.test"
        assert port == 993
        assert timeout == 30
        assert ssl_context.check_hostname
        assert ssl_context.verify_mode == ssl.CERT_REQUIRED
        return fake
    monkeypatch.setattr(subject.imaplib, "IMAP4_SSL", secure)
    return fake


@pytest.fixture
def config():
    return subject.MailConfig("imap.example.test", 993, "inbox@example.test", "synthetic-secret")


@pytest.fixture(autouse=True)
def forbid_real_socket(monkeypatch):
    import socket
    def forbidden(*args, **kwargs):
        raise AssertionError("A mailbox test attempted a real network connection.")
    monkeypatch.setattr(socket, "create_connection", forbidden)


def test_probe_starts_at_current_uidnext_without_reading_messages(connection, config):
    with subject.IMAPMailbox(config) as mailbox:
        cursor = mailbox.probe()
        assert cursor == subject.MailboxCursor(uid_validity="123", last_uid=10)
    assert not any(call[0] in {"SEARCH", "FETCH"} for call in connection.calls)
    assert connection.calls[-1] == ("LOGOUT",)


def test_incremental_search_includes_already_read_mail_and_orders_oldest_first(connection, config):
    with subject.IMAPMailbox(config) as mailbox:
        assert mailbox.list_uids(after_uid=7, uid_validity="123", limit=2) == [8, 9]
    searches = [call for call in connection.calls if call[0] == "SEARCH"]
    assert searches == [("SEARCH", None, "UID", "8:*")]
    assert "UNSEEN" not in str(searches)


def test_reversed_imap_star_range_does_not_repeat_old_uid(connection, config):
    connection.search = b"10"
    with subject.IMAPMailbox(config) as mailbox:
        assert mailbox.list_uids(after_uid=10, uid_validity="123") == []


def test_uidvalidity_reset_stops_before_search_or_body_fetch(connection, config):
    with subject.IMAPMailbox(config) as mailbox:
        with pytest.raises(MailAdapterError) as error:
            mailbox.list_uids(after_uid=7, uid_validity="122")
    assert error.value.code == "uidvalidity_changed"
    assert "Reconnecting alone cannot restore sync" in str(error.value)
    assert not any(call[0] in {"SEARCH", "FETCH"} for call in connection.calls)


def test_fetch_peeks_with_size_preflight_and_leaves_all_flags_unchanged(connection, config):
    with subject.IMAPMailbox(config) as mailbox:
        result = mailbox.fetch(9, uid_validity="123")
    fetches = [call for call in connection.calls if call[0] == "FETCH"]
    assert fetches[0] == ("FETCH", "9", "(UID RFC822.SIZE)")
    assert "BODY.PEEK[]<0.31457281>" in fetches[1][2]
    assert {call[0] for call in connection.calls} <= {"LOGIN", "SELECT", "FETCH", "LOGOUT"}
    assert result.uid == 9 and result.uid_validity == "123"
    assert result.received_at == datetime(2026, 10, 20, 7, 0, tzinfo=timezone.utc)
    assert result.unread
    assert result.from_address == "applicant@example.test"
    assert result.subject == "Synthetic application"
    assert result.attachments[0].mime == "application/pdf"
    assert result.attachments[0].filename == "misnamed-photo.jpg"
    assert result.attachments[0].data.startswith(b"%PDF-")


def test_seen_flag_is_read_without_being_modified(connection, config):
    connection.flags = b"(\\SEEN \\Answered)"
    with subject.IMAPMailbox(config) as mailbox:
        assert mailbox.fetch(9, uid_validity="123").unread is False
    assert not any(call[0] == "STORE" for call in connection.calls)


def test_oversized_message_is_rejected_before_body_download(connection, config):
    connection.size = config.max_message_bytes + 1
    with subject.IMAPMailbox(config) as mailbox:
        with pytest.raises(MailMessageError) as error:
            mailbox.fetch(9, uid_validity="123")
    assert error.value.code == "message_too_large"
    assert error.value.uid == 9
    assert error.value.provider_id.endswith(":123:9")
    assert len([call for call in connection.calls if call[0] == "FETCH"]) == 1


def test_incomplete_message_is_not_parsed_as_success(connection, config):
    connection.size += 50
    with subject.IMAPMailbox(config) as mailbox:
        with pytest.raises(MailMessageError) as error:
            mailbox.fetch(9, uid_validity="123")
    assert error.value.code == "incomplete_message"
    assert error.value.retryable


def test_unsolicited_other_uid_cannot_be_attached_to_requested_message(connection, config):
    connection.fetch_uid = 8
    with subject.IMAPMailbox(config) as mailbox:
        with pytest.raises(MailMessageError) as error:
            mailbox.fetch(9, uid_validity="123")
    assert error.value.code == "fetch_failed"


def test_source_identifiers_are_stable_and_scoped_to_folder_validity_and_account(connection, config):
    with subject.IMAPMailbox(config) as mailbox:
        first = mailbox.fetch(9, uid_validity="123")
        again = mailbox.fetch(9, uid_validity="123")
    assert first.provider_id == again.provider_id
    assert first.attachments[0].provider_attachment_id == again.attachments[0].provider_attachment_id
    assert first.source_key("account-A") != first.source_key("account-B")
    assert first.provider_id != subject.provider_message_id("Archive", "123", 9)
    assert first.provider_id != subject.provider_message_id("INBOX", "124", 9)
    assert len(subject.provider_message_id("A" * 500, "123", 9)) < 255


def test_provider_errors_do_not_leak_credentials_or_server_response(connection, config):
    connection.login_error = RuntimeError("synthetic-secret AUTH failed for inbox@example.test")
    with pytest.raises(MailAdapterError) as error:
        with subject.IMAPMailbox(config):
            pass
    assert error.value.code == "authentication_failed"
    assert "synthetic-secret" not in str(error.value)
    assert "inbox@example.test" not in str(error.value)
    assert "synthetic-secret" not in repr(config)
    assert connection.calls[-1] == ("LOGOUT",)


def test_starttls_precedes_any_credentials(monkeypatch, config):
    fake = FakeIMAP()
    monkeypatch.setattr(subject.imaplib, "IMAP4", lambda *args, **kwargs: fake)
    with subject.IMAPMailbox(replace(config, use_ssl=False, port=143)):
        pass
    assert [call[0] for call in fake.calls] == ["STARTTLS", "LOGIN", "LOGOUT"]


def test_failed_starttls_never_sends_password_and_closes_socket(monkeypatch, config):
    fake = FakeIMAP()
    fake.tls_error = RuntimeError("TLS failed")
    monkeypatch.setattr(subject.imaplib, "IMAP4", lambda *args, **kwargs: fake)
    with pytest.raises(MailAdapterError):
        with subject.IMAPMailbox(replace(config, use_ssl=False, port=143)):
            pass
    assert [call[0] for call in fake.calls] == ["STARTTLS", "LOGOUT"]


def parse(raw):
    return parse_message(raw, provider_id="imap:folder:123:9", uid=9, uid_validity="123", folder="INBOX",
                         received_at=datetime(2026, 10, 20, tzinfo=timezone.utc), unread=True)


def test_unnamed_inline_pdf_and_image_are_not_lost():
    message = EmailMessage()
    message.set_content("Application text")
    message.add_attachment(b"%PDF-1.7\ninline", maintype="application", subtype="octet-stream",
                           disposition="inline")
    message.add_attachment(b"\x89PNG\r\n\x1a\nimage", maintype="application", subtype="octet-stream",
                           disposition="inline")
    parsed = parse(message.as_bytes())
    assert [part.mime for part in parsed.attachments] == ["application/pdf", "image/png"]
    assert [part.filename.rsplit(".", 1)[-1] for part in parsed.attachments] == ["pdf", "png"]
    assert len({part.provider_attachment_id for part in parsed.attachments}) == 2


def test_plain_body_is_not_an_attachment_and_html_is_not_executable():
    message = EmailMessage()
    message.set_content("Readable plain body")
    message.add_alternative("<p>Other HTML body</p>", subtype="html")
    assert parse(message.as_bytes()).attachments == ()
    assert parse(message.as_bytes()).body.strip() == "Readable plain body"
    html = EmailMessage()
    html.set_content("<p>Application &amp; details</p><script>bad()</script>", subtype="html")
    result = parse(html.as_bytes())
    assert result.body == "Application & details"
    assert "bad()" not in result.body


def test_plain_body_discussing_pdf_magic_is_not_a_document_attachment():
    message = EmailMessage()
    message.set_content("The parser reported %PDF-1.7 in its output. Please investigate.")
    result = parse(message.as_bytes())
    assert not result.attachments
    assert "%PDF-1.7" in result.body


def test_duplicate_filenames_have_distinct_part_ids_but_same_content_hash():
    message = EmailMessage()
    message.set_content("Two copies")
    for _ in range(2):
        message.add_attachment(b"%PDF-1.7\nsame bytes", maintype="application", subtype="pdf", filename="cv.pdf")
    attachments = parse(message.as_bytes()).attachments
    assert attachments[0].provider_attachment_id != attachments[1].provider_attachment_id
    assert attachments[0].content_hash == attachments[1].content_hash


def test_path_like_filename_is_safe_but_never_used_to_classify():
    message = EmailMessage()
    message.set_content("File")
    message.add_attachment(b"%PDF-1.7\nbytes", maintype="image", subtype="png",
                           filename=r"..\..\folder/scan.png")
    attachment = parse(message.as_bytes()).attachments[0]
    assert attachment.filename == "scan.png"
    assert attachment.mime == "application/pdf"


def test_unknown_and_empty_explicit_attachments_remain_visible_for_failure_recording():
    message = EmailMessage()
    message.set_content("Damaged files")
    message.add_attachment(b"not a pdf", maintype="application", subtype="pdf", filename="cv.pdf")
    message.add_attachment(b"", maintype="application", subtype="octet-stream", filename="empty.pdf")
    attachments = parse(message.as_bytes()).attachments
    assert len(attachments) == 2
    assert all(part.mime == "application/octet-stream" for part in attachments)
    assert attachments[1].data == b""


def test_docx_is_detected_from_zip_content_not_extension():
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("word/document.xml", "<document/>")
    assert document_mime(out.getvalue()).endswith("wordprocessingml.document")
    assert document_mime(b"PKbroken") is None


def test_malformed_mime_is_a_visible_message_failure():
    raw = b'MIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary="missing"\r\n\r\nno boundary'
    with pytest.raises(MailMessageError) as error:
        parse(raw)
    assert error.value.code == "malformed_message"


def test_reported_body_truncation_does_not_truncate_attachment_bytes():
    message = EmailMessage()
    message.set_content("x" * 100_100)
    original = b"%PDF-1.7\n" + b"a" * 150_000
    message.add_attachment(original, maintype="application", subtype="pdf", filename="cv.pdf")
    result = parse(message.as_bytes())
    assert len(result.body) == 100_000
    assert result.attachments[0].data == original
    assert result.warnings


def test_folder_quoting_handles_spaces_quotes_and_non_ascii():
    assert subject._mailbox_wire_name("Job Applications") == '"Job Applications"'
    assert subject._mailbox_wire_name('One "Folder"') == '"One \\"Folder\\""'
    assert subject._mailbox_wire_name("A&B") == '"A&-B"'
    assert subject._mailbox_wire_name("தமிழ்").isascii()


@pytest.mark.parametrize("updates", [
    {"host": "https://imap.example.test"}, {"port": 0}, {"password": ""},
    {"folder": "INBOX\r\nSTORE"}, {"timeout": 0}, {"timeout": float("inf")},
])
def test_invalid_configuration_is_rejected_before_connection(config, updates):
    with pytest.raises(MailAdapterError) as error:
        replace(config, **updates)
    assert error.value.code == "invalid_config"
