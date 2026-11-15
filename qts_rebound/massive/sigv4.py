"""AWS Signature Version 4 for Massive's S3-compatible flat-file endpoint (standard library only).

Massive's endpoint is ``https://files.massive.com``, bucket ``flatfiles``, region ``us-east-1``,
path-style URLs. Only GET requests without a body are needed (object downloads and bucket
listings), so the payload hash is always the SHA-256 of the empty string.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import hmac
from collections.abc import Iterable, Mapping
from typing import Tuple, Union  # noqa: UP035 (the Query alias is evaluated at runtime on 3.9)
from urllib.parse import quote

ALGORITHM = "AWS4-HMAC-SHA256"
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()

Query = Union[Mapping[str, str], Iterable[Tuple[str, str]]]  # noqa: UP006, UP007 (runs on 3.9)


def uri_encode(s: str, keep_slash: bool) -> str:
    """RFC 3986 encoding as SigV4 wants it: everything except A-Z a-z 0-9 - _ . ~ (and / in paths)."""
    return quote(s, safe="-_.~/" if keep_slash else "-_.~")


def canonical_query(query: Query | None) -> str:
    """Sorted, encoded ``name=value`` pairs; also used verbatim as the URL's query string so the
    request sent is byte-for-byte the one that was signed."""
    if not query:
        return ""
    items = query.items() if isinstance(query, Mapping) else query
    pairs = sorted((uri_encode(str(k), False), uri_encode(str(v), False)) for k, v in items)
    return "&".join(k + "=" + v for k, v in pairs)


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def signing_key(secret_key: str, datestamp: str, region: str, service: str) -> bytes:
    k = _hmac(("AWS4" + secret_key).encode("utf-8"), datestamp)
    k = _hmac(k, region)
    k = _hmac(k, service)
    return _hmac(k, "aws4_request")


def canonical_request(
    method: str, path: str, query: Query | None, headers: Mapping[str, str], payload_hash: str
) -> tuple[str, str]:
    """Returns (canonical request, signed header list). ``headers`` must already contain host,
    x-amz-date and x-amz-content-sha256."""
    h = {k.lower().strip(): " ".join(str(v).strip().split()) for k, v in headers.items()}
    names = sorted(h)
    canon_headers = "".join(n + ":" + h[n] + "\n" for n in names)
    signed = ";".join(names)
    creq = "\n".join(
        [method.upper(), uri_encode(path, True), canonical_query(query), canon_headers, signed, payload_hash]
    )
    return creq, signed


def sign(
    method: str,
    host: str,
    path: str,
    query: Query | None,
    access_key: str,
    secret_key: str,
    *,
    region: str = "us-east-1",
    service: str = "s3",
    headers: Mapping[str, str] | None = None,
    now: _dt.datetime | None = None,
    payload_hash: str = EMPTY_SHA256,
) -> dict:
    """Headers to send with the request: the given ones plus x-amz-date, x-amz-content-sha256
    and Authorization. ``host`` is the URL's netloc (with the port if it has one); the HTTP
    library sends it as the Host header itself, so it is signed but not returned."""
    now = now or _dt.datetime.now(_dt.timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    datestamp = amz_date[:8]
    h = {k.lower(): v for k, v in (headers or {}).items()}
    h.update({"host": host, "x-amz-date": amz_date, "x-amz-content-sha256": payload_hash})
    creq, signed = canonical_request(method, path, query, h, payload_hash)
    scope = datestamp + "/" + region + "/" + service + "/aws4_request"
    to_sign = "\n".join([ALGORITHM, amz_date, scope, hashlib.sha256(creq.encode("utf-8")).hexdigest()])
    signature = hmac.new(
        signing_key(secret_key, datestamp, region, service), to_sign.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    out = {k: v for k, v in h.items() if k != "host"}
    out["authorization"] = (
        ALGORITHM
        + " Credential="
        + access_key
        + "/"
        + scope
        + ", SignedHeaders="
        + signed
        + ", Signature="
        + signature
    )
    return out
