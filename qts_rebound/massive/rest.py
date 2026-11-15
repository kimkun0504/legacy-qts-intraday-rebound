"""Massive's REST API (reference data), standard library only.

The API key travels as the ``apiKey`` query parameter, as in Massive's documentation; URLs are
therefore never printed or logged, and errors carry only the HTTP status and Massive's message.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlencode, urlsplit, urlunsplit

from qts_rebound.massive.s3 import AuthError, Retryable, S3Error, ssl_context

API = "https://api.massive.com"


def _with_key(url: str, api_key: str) -> str:
    u = urlsplit(url)
    q = u.query + ("&" if u.query else "") + urlencode({"apiKey": api_key})
    return urlunsplit((u.scheme, u.netloc, u.path, q, ""))


class Rest:
    def __init__(self, api_key: str, base: str = API, timeout: float = 60.0) -> None:
        self._key = api_key
        self._base = base.rstrip("/")
        self._timeout = timeout
        self._ctx = ssl_context() if self._base.startswith("https") else None

    def __repr__(self) -> str:
        return f"Rest({self._base})"

    def get_json(self, path_or_url: str, params: dict[str, str] | None = None, attempts: int = 6) -> dict:
        url = path_or_url if "://" in path_or_url else self._base + path_or_url
        if params:
            url += ("&" if "?" in url else "?") + urlencode(params)
        url = _with_key(url, self._key)
        for i in range(attempts):
            try:
                with urllib.request.urlopen(url, timeout=self._timeout, context=self._ctx) as r:
                    return json.loads(r.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                try:
                    msg = json.loads(e.read(4096).decode("utf-8", "replace")).get("message", "")
                except Exception:
                    msg = ""
                detail = f"HTTP {e.code} {msg}".strip()
                if e.code in (401, 403):
                    raise AuthError(detail) from None
                if e.code != 429 and e.code < 500:
                    raise S3Error(detail) from None
                err: Exception = Retryable(detail)
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                err = Retryable(f"network: {getattr(e, 'reason', e)}")
            if i == attempts - 1:
                raise err
            time.sleep(min(60.0, 2.0 ** (i + 1)))
        raise AssertionError("unreachable")

    def paginate(self, path: str, params: dict[str, str]) -> Iterator[dict]:
        """Yields the ``results`` of every page, following ``next_url``."""
        page = self.get_json(path, params)
        while True:
            yield from page.get("results") or []
            nxt = page.get("next_url")
            if not nxt:
                return
            page = self.get_json(nxt)


def download_tickers(rest: Rest, out_dir: Path, printer=print) -> dict[str, int]:
    """All US stock tickers Massive knows, active and delisted, one JSON object per line."""
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = _dt.date.today().strftime("%Y%m%d")
    counts = {}
    for active in ("true", "false"):
        name = "active" if active == "true" else "delisted"
        path = out_dir / f"tickers_{name}_{stamp}.jsonl"
        tmp = path.with_name(path.name + ".tmp")
        n = 0
        with open(tmp, "w", encoding="utf-8") as f:
            for rec in rest.paginate(
                "/v3/reference/tickers",
                {"market": "stocks", "active": active, "limit": "1000", "order": "asc", "sort": "ticker"},
            ):
                f.write(json.dumps(rec) + "\n")
                n += 1
                if n % 5000 == 0:
                    printer(f"  {name}: {n:,} tickers so far")
        os.replace(tmp, path)
        counts[name] = n
        printer(f"  {name}: {n:,} tickers -> {path}")
    return counts
