"""Importing one candidate: the files go to the object store side by side, nothing is half stored, nothing is uploaded twice.

A fake object store that sleeps for every put stands in for the internet link (the real store and the real database are never
touched); disposable PostgreSQL for the rows. Synthetic data only.
"""
import json
import threading
import time
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.db.models import Attachment, Candidate
from app.services import bulk_candidate_import as bulk, document_storage
from test_bulk_candidate_import import ALL_DOCUMENTS, PDF, env, subject  # noqa: F401  (fixture)
from test_database_naming import schema_database  # noqa: F401

PUT_SECONDS = 0.3


class Store:
    """An object store reached over a slow link: every put takes ``PUT_SECONDS``; records what is stored and deleted."""

    def __init__(self, seconds=PUT_SECONDS, fail_on=None):
        self.seconds, self.fail_on = seconds, fail_on
        self.lock = threading.Lock()
        self.objects, self.deleted, self.started, self.running, self.peak = {}, [], 0, 0, 0

    def put(self, settings, key, data, content_type="application/octet-stream"):
        with self.lock:
            self.started += 1
            number = self.started
            self.running += 1
            self.peak = max(self.peak, self.running)
        try:
            time.sleep(self.seconds)
            if self.fail_on == number:
                raise OSError("the link dropped")
            with self.lock:
                self.objects[key] = data
            return key
        finally:
            with self.lock:
                self.running -= 1

    def delete(self, settings, key):
        with self.lock:
            self.deleted.append(key)
            self.objects.pop(key, None)


def remote(monkeypatch, store, base):
    settings = SimpleNamespace(**{**vars(base), "storage_backend": "s3", "s3_bucket": "bucket", "s3_prefix": "crm"})
    for target in (bulk, subject):
        monkeypatch.setattr(target, "get_settings", lambda: settings)
    monkeypatch.setattr(document_storage, "put_bytes", store.put)
    monkeypatch.setattr(document_storage, "delete_bytes", store.delete)
    return settings


def post(env, folder, names, row=None):
    spec = {"folder": folder, "row": {"name": bulk._clean_name(folder), **(row or {})}, "files": [{"path": n} for n in names],
            "defaultOrder": None, "defaultRecruiterId": "staff"}
    files = [("files", (n, (PDF if not n.endswith((".jpg", ".png")) else b"JPEGDATA") + n.encode() + folder.encode(),
                        "application/pdf" if not n.endswith((".jpg", ".png")) else "image/jpeg")) for n in names]
    return env.client.post("/api/candidate-import/candidate", data={"spec": json.dumps(spec)}, files=files)


# ------------------------------------------------------------------ store_many on its own

def items(count, prefix="file"):
    return [(f"{prefix}-{n}".encode() * 10, f"document-{n}.pdf", "application/pdf") for n in range(count)]


def remote_settings():
    return SimpleNamespace(storage_backend="s3", s3_bucket="bucket", s3_prefix="crm", storage_dir="unused")


def test_twelve_files_go_up_four_at_a_time_and_come_back_in_the_order_given(monkeypatch):
    store = Store(seconds=0.1)
    monkeypatch.setattr(document_storage, "put_bytes", store.put)
    written: list[str] = []
    started = time.perf_counter()
    stored = bulk.store_many(remote_settings(), items(12), written)
    took = time.perf_counter() - started
    assert [digest for _, digest, _ in stored] == [bulk.hashlib.sha256(payload).hexdigest() for payload, _, _ in items(12)]
    assert len({key for key, _, _ in stored}) == 12 and set(written) == {key for key, _, _ in stored} == set(store.objects)
    assert 2 <= store.peak <= bulk.STORE_THREADS
    assert took < 12 * 0.1 * 0.6, took                     # twelve in a row would take 1.2 s; four at a time about 0.3 s


def test_local_disk_is_written_one_file_after_another(monkeypatch, tmp_path):
    from app.api.routers import files as files_router
    monkeypatch.setattr(files_router, "STORAGE", tmp_path)
    settings = SimpleNamespace(storage_backend="local", storage_dir=str(tmp_path))
    store = Store(seconds=0.0)
    monkeypatch.setattr(document_storage, "put_bytes", store.put)
    written: list[str] = []
    stored = bulk.store_many(settings, items(5), written)
    assert len(stored) == 5 and store.started == 0 and all(path.is_file() for path in tmp_path.glob("*/*"))


def test_the_same_bytes_are_stored_once_and_share_the_key(monkeypatch):
    store = Store(seconds=0.01)
    monkeypatch.setattr(document_storage, "put_bytes", store.put)
    same = [(b"same bytes", "a.pdf", "application/pdf"), (b"other bytes", "b.pdf", "application/pdf"), (b"same bytes", "c.pdf", "application/pdf")]
    written: list[str] = []
    stored = bulk.store_many(remote_settings(), same, written)
    assert store.started == 2 and len(written) == 2
    assert stored[0][0] == stored[2][0] != stored[1][0] and stored[2][2] == 0.0 and stored[0][1] == stored[2][1]


def test_when_one_upload_fails_the_rest_are_not_started_and_every_stored_key_is_reported(monkeypatch):
    store = Store(seconds=0.05, fail_on=3)
    monkeypatch.setattr(document_storage, "put_bytes", store.put)
    monkeypatch.setattr(document_storage, "delete_bytes", store.delete)
    written: list[str] = []
    with pytest.raises(OSError, match="link dropped"):
        bulk.store_many(remote_settings(), items(12), written)
    assert store.started < 12                                       # uploads that had not begun were skipped
    assert set(written) == set(store.objects) and written       # exactly what DID get stored, so it can be taken away again
    bulk.discard_written(remote_settings(), written)
    assert store.objects == {} and set(store.deleted) == set(written)


# ------------------------------------------------------------------ one whole import

NAMES = ALL_DOCUMENTS[:12]


def test_a_twelve_file_candidate_imports_in_a_third_of_the_time_and_says_where_the_time_went(env, monkeypatch):
    store = Store()
    remote(monkeypatch, store, bulk.get_settings())
    monkeypatch.setattr(bulk, "STORE_THREADS", 1)
    started = time.perf_counter()
    before = post(env, "Serial", NAMES, {"phone": "9876500001"})
    sequential = time.perf_counter() - started
    assert before.status_code == 200 and before.json()["status"] == "imported"
    monkeypatch.setattr(bulk, "STORE_THREADS", 4)
    started = time.perf_counter()
    after = post(env, "Parallel", NAMES, {"phone": "9876500002"})
    parallel = time.perf_counter() - started
    assert after.status_code == 200 and after.json()["status"] == "imported"
    print(f"\nIMPORT 12 files, {PUT_SECONDS}s per put (fake): one at a time {sequential:.2f}s, four at a time {parallel:.2f}s")
    assert sequential >= 12 * PUT_SECONDS
    assert parallel < sequential * 0.5, (sequential, parallel)
    with env.sessions() as db:
        slots = sorted(row.slot for row in db.scalars(select(Attachment).where(Attachment.entity_type == "candidate")))
        assert len(slots) >= 2 * 12 - 2 and db.scalar(select(func.count()).select_from(Candidate)) == 2
    timings = after.json()["timings"]
    assert timings["totalSeconds"] >= timings["storeSeconds"] >= 0.9 and timings["checkSeconds"] >= 0
    assert {"receiveSeconds", "readUnnamedSeconds", "saveSeconds", "commitSeconds"} <= set(timings)
    assert [item["index"] for item in timings["perFile"]] == list(range(len(timings["perFile"]))) and len(timings["perFile"]) == 12
    assert all(item["storeSeconds"] >= PUT_SECONDS * 0.9 for item in timings["perFile"] if not item["sharedKey"])
    flat = json.dumps(timings)
    for name in NAMES:                                  # positions only: no file names, no candidate names
        assert name.split(".")[0] not in flat
    assert "Parallel" not in flat


def test_a_failed_upload_fails_the_whole_candidate_with_nothing_stored_and_nothing_written(env, monkeypatch):
    store = Store(seconds=0.05, fail_on=3)
    remote(monkeypatch, store, bulk.get_settings())
    response = post(env, "Broken", NAMES, {"phone": "9876500003"})
    assert response.status_code == 503, response.text
    with env.sessions() as db:
        assert db.scalar(select(func.count()).select_from(Candidate)) == 0
        assert db.scalar(select(func.count()).select_from(Attachment)) == 0
    assert store.objects == {} and store.deleted                           # what was already up is taken away again
    store.fail_on = None                                                   # the same request, retried, now works completely
    assert post(env, "Broken", NAMES, {"phone": "9876500003"}).status_code == 200
    with env.sessions() as db:
        assert db.scalar(select(func.count()).select_from(Candidate)) == 1


def test_importing_the_same_candidate_again_uploads_nothing(env, monkeypatch):
    store = Store(seconds=0.01)
    remote(monkeypatch, store, bulk.get_settings())
    names = ["Resume.pdf", "Passport.pdf"]
    first = post(env, "Twice", names, {"phone": "9876500004"}).json()
    assert first["status"] == "imported"
    uploaded = store.started
    second = post(env, "Twice", names, {"phone": "9876500004"}).json()
    assert second["status"] in {"duplicate", "already_imported"} and second.get("careId") == first["careId"]
    assert store.started == uploaded                                       # the duplicate is found before anything is uploaded
    with env.sessions() as db:
        assert db.scalar(select(func.count()).select_from(Candidate)) == 1
