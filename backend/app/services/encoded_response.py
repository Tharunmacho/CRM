"""A JSON payload encoded (and compressed) once, so a repeat answer costs a socket write.

FastAPI turns a returned dict into JSON with `jsonable_encoder`, a recursive Python walk: about 1.3 s for the
8 MB desk snapshot of a 4,000-candidate agency, against 0.08 s for `json.dumps` of the same, already
JSON-shaped, data. This helper encodes straight away, compresses in the worker thread (not in the event loop,
which the gzip middleware would block for ~200 ms on that payload) and hands out the finished bytes.
"""

from __future__ import annotations

import gzip
import hashlib
import json

from fastapi import Request, Response
from fastapi.encoders import jsonable_encoder

MIN_COMPRESS_BYTES = 1024
KEEP_RAW_BELOW = 64 * 1024  # larger payloads keep only the gzip form (a 9 MB desk per user would not fit in memory for a team)
_GZIP_LEVEL = 5


class Encoded:
    """JSON bytes plus their gzip form. Payloads of 1 KB or more are compressed when built (in the caller's worker thread);
    large ones then keep only the compressed bytes and are inflated again on the rare request that does not accept gzip."""

    __slots__ = ("_raw", "_gz", "size", "etag")

    def __init__(self, raw: bytes, etag: str | None = None) -> None:
        self.etag = etag
        self.size = len(raw)
        self._gz = gzip.compress(raw, _GZIP_LEVEL, mtime=0) if self.size >= MIN_COMPRESS_BYTES else None
        self._raw = raw if self._gz is None or self.size < KEEP_RAW_BELOW else None

    @classmethod
    def of(cls, data, *, json_ready: bool = False, revision: bool = False) -> "Encoded":
        """Encode `data`. Pass json_ready=True when it is already plain JSON types (e.g. `model_dump(mode="json")`).

        `revision=True` (a JSON object only) stamps the body with `"revision"`, a short digest of everything else in it,
        and serves the same digest as the ETag: a browser that already holds this exact body sends it back in
        If-None-Match and gets an empty 304 instead of the whole payload. The digest is of the content itself, so it
        can never say "unchanged" about a body that differs, and it differs for every person (their data is in it).
        """
        body = data if json_ready else jsonable_encoder(data)
        raw = json.dumps(body, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        if not revision:
            return cls(raw)
        digest = hashlib.blake2b(raw, digest_size=12).hexdigest()
        return cls(raw[:-1] + b',"revision":"' + digest.encode() + b'"}', digest)

    @property
    def raw(self) -> bytes:
        return self._raw if self._raw is not None else gzip.decompress(self._gz)

    def response(self, request: Request, headers: dict[str, str] | None = None) -> Response:
        out = dict(headers or {})
        out.setdefault("Cache-Control", "private, no-store")
        if self._gz is not None:
            out["Vary"] = "Accept-Encoding"
        if self.etag is not None:
            out["ETag"] = f'"{self.etag}"'
            if _holds(request.headers.get("if-none-match"), self.etag):
                return Response(status_code=304, headers=out)
        if self._gz is not None:
            if "gzip" in request.headers.get("accept-encoding", ""):
                out["Content-Encoding"] = "gzip"
                return Response(self._gz, media_type="application/json", headers=out)
        return Response(self.raw, media_type="application/json", headers=out)


def _holds(header: str | None, etag: str) -> bool:
    """True when an If-None-Match header names this ETag (weak or strong form, one or several tags; `*` never matches)."""
    if not header:
        return False
    return any(tag.strip().removeprefix("W/").strip('"') == etag for tag in header.split(","))
