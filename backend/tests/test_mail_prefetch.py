"""Listing a folder page downloads its messages in a few batched round trips."""
from email.message import EmailMessage

from app.integrations.mail import imap as subject
from app.integrations.mail.mailbox import WritableMailbox


def raw_message(number: int) -> bytes:
    message = EmailMessage()
    message["From"] = f"Applicant {number} <applicant{number}@example.test>"
    message["To"] = "jobs@example.test"
    message["Subject"] = f"Application {number}"
    message["Date"] = "Mon, 19 Oct 2026 11:00:00 +0530"
    message.set_content(f"Body {number}")
    return message.as_bytes()


class BatchIMAP:
    def __init__(self, count: int):
        self.messages = {uid: raw_message(uid) for uid in range(1, count + 1)}
        self.fetch_calls: list[str] = []

    def select(self, folder, readonly=True):
        return "OK", [b"1"]

    def response(self, key):
        return key, {"UIDVALIDITY": [b"123"], "UIDNEXT": [str(len(self.messages) + 1).encode()]}[key]

    def uid(self, command, *args):
        if command == "SEARCH":
            return "OK", [" ".join(map(str, self.messages)).encode()]
        assert command == "FETCH"
        targets = [int(item) for item in args[0].split(",")]
        self.fetch_calls.append(args[1])
        if "RFC822.SIZE" in args[1]:
            return "OK", [f"{uid} (UID {uid} RFC822.SIZE {len(self.messages[uid])})".encode() for uid in targets]
        rows = []
        for uid in targets:
            rows.append((f'{uid} (UID {uid} INTERNALDATE "19-Oct-2026 11:00:00 +0530" FLAGS (\\Seen) BODY[] {{{len(self.messages[uid])}}}'.encode(), self.messages[uid]))
            rows.append(b")")
        return "OK", rows


def mailbox(count):
    box = WritableMailbox(subject.MailConfig("imap.example.test", 993, "jobs@example.test", "synthetic-secret"))
    box._connection = BatchIMAP(count)
    return box


def test_a_page_of_messages_costs_a_handful_of_server_calls_not_two_per_message():
    box = mailbox(25)
    _, total, rows = box.page("INBOX", limit=25)
    assert total == 25 and len(rows) == 25
    assert sorted(item.subject for item in rows) == sorted(f"Application {number}" for number in range(1, 26))
    assert all(item.unread is False and item.uid for item in rows)
    assert len(box._connection.fetch_calls) == 2  # one size query and one body batch


def test_large_pages_split_into_bounded_body_batches(monkeypatch):
    box = mailbox(6)
    size = len(box._connection.messages[1])
    box.prefetch(list(range(1, 7)), batch_bytes=size * 2 + 1)
    assert len(box._connection.fetch_calls) == 1 + 3  # sizes, then three batches of two


def test_prefetch_failure_falls_back_to_single_message_fetches():
    box = mailbox(3)
    original = box._connection.uid

    def flaky(command, *args):
        if command == "FETCH" and "," in args[0]:
            raise OSError("server rejected the batch")
        return original(command, *args)

    box._connection.uid = flaky
    _, total, rows = box.page("INBOX", limit=3)
    assert total == 3 and len(rows) == 3
