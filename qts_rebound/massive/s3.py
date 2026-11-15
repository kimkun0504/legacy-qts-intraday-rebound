"""Minimal S3 client for Massive's flat files: bucket listings and streamed object downloads.

Standard library only. Errors are sorted into the ones worth retrying (network trouble, 5xx,
429, short or corrupt transfers) and the ones that are not (bad keys or a plan without access:
401/403; a file that does not exist: 404). Error messages carry the S3 error code and message
only, never request headers or signed URLs.
"""

from __future__ import annotations

import builtins
import hashlib
import http.client
import re
import ssl
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlsplit

from qts_rebound.massive.sigv4 import canonical_query, sign

ENDPOINT = "https://files.massive.com"
BUCKET = "flatfiles"
CHUNK = 1 << 20


class S3Error(RuntimeError):
    pass


class AuthError(S3Error):
    """401/403: wrong keys, or the plan does not include this file. Not retried."""


class NotFound(S3Error):
    """404: no such file (a holiday, or a date not published yet)."""


class Retryable(S3Error):
    """Network trouble, throttling, a server error or a damaged transfer."""


@dataclass(frozen=True)
class S3Object:
    key: str
    size: int
    etag: str
    last_modified: str


def ssl_context() -> ssl.SSLContext:
    try:  # certifi's bundle when present (python.org builds ship without system roots)
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _strip_ns(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_list_xml(body: bytes) -> tuple[list[S3Object], list[str], str | None]:
    """(objects, common prefixes, next continuation token or None) from a ListObjectsV2 reply."""
    root = ET.fromstring(body)
    objs: list[S3Object] = []
    prefixes: list[str] = []
    truncated = False
    token: str | None = None
    for el in root:
        tag = _strip_ns(el.tag)
        if tag == "Contents":
            f = {_strip_ns(c.tag): (c.text or "") for c in el}
            objs.append(
                S3Object(
                    key=f.get("Key", ""),
                    size=int(f.get("Size", "0") or 0),
                    etag=f.get("ETag", "").strip('"'),
                    last_modified=f.get("LastModified", ""),
                )
            )
        elif tag == "CommonPrefixes":
            for c in el:
                if _strip_ns(c.tag) == "Prefix" and c.text:
                    prefixes.append(c.text)
        elif tag == "IsTruncated":
            truncated = (el.text or "").strip().lower() == "true"
        elif tag == "NextContinuationToken":
            token = el.text
    return objs, prefixes, (token if truncated else None)


def _error_text(body: bytes) -> str:
    code = re.search(rb"<Code>([^<]*)</Code>", body or b"")
    msg = re.search(rb"<Message>([^<]*)</Message>", body or b"")
    parts = [m.group(1).decode("utf-8", "replace") for m in (code, msg) if m]
    return ": ".join(parts) if parts else "no error detail"


class S3Client:
    def __init__(
        self,
        access_key: str,
        secret_key: str,
        endpoint: str = ENDPOINT,
        bucket: str = BUCKET,
        region: str = "us-east-1",
        timeout: float = 60.0,
    ) -> None:
        u = urlsplit(endpoint)
        self._scheme = u.scheme
        self._host = u.netloc
        self._prefix = u.path.rstrip("/")
        self._bucket = bucket
        self._region = region
        self._ak = access_key
        self._sk = secret_key
        self._timeout = timeout
        self._ctx = ssl_context() if self._scheme == "https" else None

    def __repr__(self) -> str:  # never show the keys
        return f"S3Client({self._scheme}://{self._host}, bucket={self._bucket})"

    def _open(self, path: str, query: dict | None = None):
        headers = sign("GET", self._host, path, query, self._ak, self._sk, region=self._region)
        url = self._scheme + "://" + self._host + quote(path, safe="/-_.~")
        if query:
            url += "?" + canonical_query(query)
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            return urllib.request.urlopen(req, timeout=self._timeout, context=self._ctx)
        except urllib.error.HTTPError as e:
            try:
                body = e.read(4096)
            except Exception:
                body = b""
            detail = f"HTTP {e.code} ({_error_text(body)})"
            if e.code in (401, 403):
                raise AuthError(detail) from None
            if e.code == 404:
                raise NotFound(detail) from None
            if e.code == 429 or e.code >= 500:
                raise Retryable(detail) from None
            raise S3Error(detail) from None
        except ssl.SSLCertVerificationError as e:
            raise S3Error(
                f"TLS certificate check failed ({e.reason}). Use the Miniconda python "
                "(~/miniconda3/bin/python3) or run 'pip install certifi'."
            ) from None
        except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException) as e:
            raise Retryable(f"network: {getattr(e, 'reason', e)}") from None

    def _bucket_path(self, key: str = "") -> str:
        return self._prefix + "/" + self._bucket + ("/" + key if key else "")

    def list_page(
        self, prefix: str, delimiter: str | None = None, token: str | None = None, max_keys: int = 1000
    ) -> tuple[builtins.list[S3Object], builtins.list[str], str | None]:
        q = {"list-type": "2", "prefix": prefix, "max-keys": str(max_keys)}
        if delimiter:
            q["delimiter"] = delimiter
        if token:
            q["continuation-token"] = token
        with self._open(self._bucket_path(), q) as r:
            return parse_list_xml(r.read())

    def list(
        self, prefix: str, delimiter: str | None = None
    ) -> tuple[builtins.list[S3Object], builtins.list[str]]:
        objs: list[S3Object] = []
        prefixes: list[str] = []
        token: str | None = None
        while True:
            o, p, token = self.list_page(prefix, delimiter, token)
            objs += o
            prefixes += p
            if not token:
                return objs, prefixes

    def iter_get(self, key: str) -> Iterator[bytes]:
        with self._open(self._bucket_path(key)) as r:
            while True:
                b = r.read(CHUNK)
                if not b:
                    return
                yield b

    def download(self, key: str, dest_part: Path) -> tuple[int, str, str]:
        """Streams the object to ``dest_part``. Returns (bytes written, MD5 hex, ETag header).
        A transfer shorter than Content-Length raises Retryable."""
        md5 = hashlib.md5(usedforsecurity=False)
        n = 0
        dest_part.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self._open(self._bucket_path(key)) as r:
                expected = r.headers.get("Content-Length")
                etag = (r.headers.get("ETag") or "").strip('"')
                with open(dest_part, "wb") as f:
                    while True:
                        b = r.read(CHUNK)
                        if not b:
                            break
                        f.write(b)
                        md5.update(b)
                        n += len(b)
        except (TimeoutError, ConnectionError, http.client.HTTPException, ssl.SSLError) as e:
            raise Retryable(f"transfer interrupted: {e}") from None
        if expected is not None and int(expected) != n:
            raise Retryable(f"short transfer: {n} of {expected} bytes")
        return n, md5.hexdigest(), etag
