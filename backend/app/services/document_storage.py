"""Immutable local/S3 storage behind the same authenticated document endpoints."""
import hashlib
import os
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse, quote
from uuid import uuid4

from fastapi.responses import FileResponse, StreamingResponse


def _local(settings, key):
    base = Path(settings.storage_dir).resolve()
    target = (base / key).resolve()
    def comparable(path):
        # Windows can return the extended-length prefix only for a target that
        # appears during a concurrent hard-link publication. Both forms name the
        # same path; normalize the prefix only for the containment comparison.
        raw = str(path)
        if os.name == "nt" and raw.startswith("\\\\?\\UNC\\"):
            raw = "\\\\" + raw[8:]
        elif os.name == "nt" and raw.startswith("\\\\?\\"):
            raw = raw[4:]
        return Path(raw)
    if not comparable(target).is_relative_to(comparable(base)):
        raise ValueError("Invalid storage key")
    return target


def _s3(settings):
    import boto3
    from botocore.config import Config
    return boto3.client("s3", endpoint_url=getattr(settings, "s3_endpoint_url", None) or None,
        region_name=getattr(settings, "s3_region", "us-east-1") or "us-east-1",
        aws_access_key_id=getattr(settings, "s3_access_key_id", None) or None,
        aws_secret_access_key=getattr(settings, "s3_secret_access_key", None) or None,
        config=Config(connect_timeout=10, read_timeout=30, retries={"max_attempts": 2}, s3={"addressing_style": "path"}))


def storage_key(settings, key):
    if key.startswith("s3://") or getattr(settings, "storage_backend", "local") == "local":
        return key
    if settings.storage_backend != "s3" or not settings.s3_bucket:
        raise ValueError("Configure a local storage directory or S3 bucket")
    path = PurePosixPath(key)
    if path.is_absolute() or ".." in path.parts or "\\" in key:
        raise ValueError("Invalid storage key")
    prefix = getattr(settings, "s3_prefix", "crm").strip("/")
    return f"s3://{settings.s3_bucket}/{prefix + '/' if prefix else ''}{key}"


def _parts(key):
    parsed = urlparse(key)
    if parsed.scheme != "s3" or not parsed.netloc or not parsed.path.lstrip("/"):
        raise ValueError("Invalid S3 object key")
    return parsed.netloc, parsed.path.lstrip("/")


def put_bytes(settings, key, data, content_type="application/octet-stream"):
    key = storage_key(settings, key)
    digest = hashlib.sha256(data).hexdigest()
    if key.startswith("s3://"):
        from botocore.exceptions import ClientError
        client, (bucket, path) = _s3(settings), _parts(key)
        try:
            client.put_object(Bucket=bucket, Key=path, Body=data, ContentType=content_type,
                              Metadata={"sha256": digest}, IfNoneMatch="*")
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") not in {"PreconditionFailed", "412"}:
                raise
            if hashlib.sha256(read_bytes(settings, key)).hexdigest() != digest:
                raise ValueError("A different document already occupies this immutable storage key") from exc
    else:
        target = _local(settings, key)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                raise ValueError("A different document already occupies this immutable storage key")
        else:
            temporary = target.with_suffix(f".{uuid4().hex}.tmp")
            try:
                temporary.write_bytes(data)
                # Hard-link publication does not overwrite a concurrent completed upload.
                try: os.link(temporary, target)
                except FileExistsError:
                    if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                        raise ValueError("A different document already occupies this immutable storage key")
            finally:
                temporary.unlink(missing_ok=True)
    return key


def read_bytes(settings, key):
    if not key.startswith("s3://"):
        return _local(settings, key).read_bytes()
    bucket, path = _parts(key)
    body = _s3(settings).get_object(Bucket=bucket, Key=path)["Body"]
    try:
        return body.read()
    finally:
        body.close()


def exists(settings, key):
    if not key.startswith("s3://"):
        return _local(settings, key).is_file()
    from botocore.exceptions import ClientError
    bucket, path = _parts(key)
    try:
        _s3(settings).head_object(Bucket=bucket, Key=path)
        return True
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
            return False
        raise


def delete_bytes(settings, key):
    if key.startswith("s3://"):
        bucket, path = _parts(key)
        _s3(settings).delete_object(Bucket=bucket, Key=path)
    else:
        _local(settings, key).unlink(missing_ok=True)


def file_response(settings, key, filename, content_type, download=False):
    headers = {"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"}
    disposition = "attachment" if download else "inline"
    if not key.startswith("s3://"):
        path = _local(settings, key)
        if not path.is_file():
            raise FileNotFoundError("The stored document is unavailable")
        return FileResponse(path, filename=filename, media_type=content_type, content_disposition_type=disposition, headers=headers)
    bucket, path = _parts(key)
    result = _s3(settings).get_object(Bucket=bucket, Key=path)
    body = result["Body"]
    def chunks():
        try:
            yield from body.iter_chunks(chunk_size=65536)
        finally:
            body.close()
    headers["Content-Disposition"] = f"{disposition}; filename*=UTF-8''{quote(filename, safe='')}"
    if result.get("ContentLength") is not None:
        headers["Content-Length"] = str(result["ContentLength"])
    return StreamingResponse(chunks(), media_type=content_type, headers=headers)
