from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from types import SimpleNamespace
import hashlib

import boto3
from botocore.response import StreamingBody
from botocore.stub import Stubber
import pytest

from app.services import document_storage as storage


def test_local_immutable_publication_and_concurrent_replay(tmp_path):
    settings = SimpleNamespace(storage_dir=str(tmp_path), storage_backend="local")
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(lambda _: storage.put_bytes(settings, "candidate/source.pdf", b"original"), range(4))) == ["candidate/source.pdf"] * 4
    assert storage.read_bytes(settings, "candidate/source.pdf") == b"original"
    assert storage.exists(settings, "candidate/source.pdf")
    with pytest.raises(ValueError, match="different document"):
        storage.put_bytes(settings, "candidate/source.pdf", b"changed")
    with pytest.raises(ValueError, match="Invalid storage key"):
        storage.read_bytes(settings, "../outside.pdf")
    assert not list(tmp_path.rglob("*.tmp"))


def test_s3_authenticated_write_read_and_checksums(monkeypatch, tmp_path):
    settings = SimpleNamespace(storage_dir=str(tmp_path), storage_backend="s3", s3_bucket="crm-test-bucket", s3_prefix="crm")
    client = boto3.client("s3", region_name="us-east-1", aws_access_key_id="synthetic", aws_secret_access_key="synthetic")
    monkeypatch.setattr(storage, "_s3", lambda _: client)
    digest = hashlib.sha256(b"original").hexdigest()
    params = {"Bucket": "crm-test-bucket", "Key": "crm/document.pdf", "Body": b"original", "ContentType": "application/pdf", "Metadata": {"sha256": digest}, "IfNoneMatch": "*"}
    with Stubber(client) as stub:
        stub.add_response("put_object", {}, params)
        key = storage.put_bytes(settings, "document.pdf", b"original", "application/pdf")
        assert key == "s3://crm-test-bucket/crm/document.pdf"
        stub.add_response("get_object", {"Body": StreamingBody(BytesIO(b"original"), 8)}, {"Bucket": "crm-test-bucket", "Key": "crm/document.pdf"})
        assert storage.read_bytes(settings, key) == b"original"
        stub.add_client_error("head_object", "NoSuchKey", http_status_code=404, expected_params={"Bucket": "crm-test-bucket", "Key": "crm/missing.pdf"})
        assert not storage.exists(settings, "s3://crm-test-bucket/crm/missing.pdf")
        stub.assert_no_pending_responses()


def test_s3_does_not_overwrite_existing_immutable_document(monkeypatch, tmp_path):
    settings = SimpleNamespace(storage_dir=str(tmp_path), storage_backend="s3", s3_bucket="crm-test-bucket", s3_prefix="")
    client = boto3.client("s3", region_name="us-east-1", aws_access_key_id="synthetic", aws_secret_access_key="synthetic")
    monkeypatch.setattr(storage, "_s3", lambda _: client)
    with Stubber(client) as stub:
        stub.add_client_error("put_object", "PreconditionFailed", http_status_code=412)
        stub.add_response("get_object", {"Body": StreamingBody(BytesIO(b"different"), 9)}, {"Bucket": "crm-test-bucket", "Key": "document.pdf"})
        with pytest.raises(ValueError, match="different document"):
            storage.put_bytes(settings, "document.pdf", b"original")


def test_blank_optional_s3_settings_use_standard_endpoint_and_credential_chain(monkeypatch):
    captured = {}
    monkeypatch.setattr(boto3, "client", lambda service, **kwargs: captured.update(kwargs) or object())
    storage._s3(SimpleNamespace(s3_endpoint_url="", s3_region="", s3_access_key_id="", s3_secret_access_key=""))
    assert captured["endpoint_url"] is None
    assert captured["region_name"] == "us-east-1"
    assert captured["aws_access_key_id"] is None and captured["aws_secret_access_key"] is None
