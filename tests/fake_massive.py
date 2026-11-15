"""A local stand-in for Massive's S3 flat-file endpoint and reference API, for tests.

It checks every request's SigV4 signature (recomputed from the request it received), serves
ListObjectsV2 pages with continuation tokens, objects with MD5 ETags, and can inject faults:
cut-off transfers, 500s, corrupted bodies, or 403s for chosen keys.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, unquote, urlsplit

from qts_rebound.massive.sigv4 import canonical_request, signing_key

ACCESS, SECRET, API_KEY = "AKFAKE0000000000", "fake/secret+key==", "fakeapikey"


def gz_csv(
    rows: list[str], header: str = "ticker,volume,open,close,high,low,window_start,transactions"
) -> bytes:
    return gzip.compress(("\n".join([header, *rows]) + "\n").encode())


class FakeMassive:
    def __init__(self, objects: dict[str, bytes], tickers: list[dict] | None = None, page_size: int = 2):
        self.objects = dict(objects)
        self.tickers = tickers or []
        self.page_size = page_size
        self.faults: dict[str, list[str]] = {}  # key -> queue of faults: cut | 500 | corrupt | 403
        self.requests: list[str] = []
        self.bad_signatures = 0
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):  # quiet
                pass

            def _verify(self, path: str, query: list[tuple[str, str]]) -> bool:
                auth = self.headers.get("Authorization", "")
                if not auth.startswith("AWS4-HMAC-SHA256 "):
                    return False
                parts = dict(p.strip().split("=", 1) for p in auth[len("AWS4-HMAC-SHA256 ") :].split(","))
                cred = parts["Credential"].split("/")
                if cred[0] != ACCESS:
                    return False
                signed = parts["SignedHeaders"].split(";")
                hdrs = {n: self.headers.get(n, "") for n in signed}
                creq, _ = canonical_request("GET", path, query, hdrs, self.headers["x-amz-content-sha256"])
                scope = "/".join(cred[1:])
                sts = "\n".join(
                    [
                        "AWS4-HMAC-SHA256",
                        self.headers["x-amz-date"],
                        scope,
                        hashlib.sha256(creq.encode()).hexdigest(),
                    ]
                )
                import hmac

                k = signing_key(SECRET, cred[1], cred[2], cred[3])
                return hmac.compare_digest(
                    hmac.new(k, sts.encode(), hashlib.sha256).hexdigest(), parts["Signature"]
                )

            def _send(
                self, code: int, body: bytes, ctype: str = "application/xml", extra: dict | None = None
            ):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                for k, v in (extra or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                u = urlsplit(self.path)
                fake.requests.append(u.path)
                if u.path.startswith("/v3/"):
                    return self._api(u)
                query = parse_qsl(u.query, keep_blank_values=True)
                if not self._verify(unquote(u.path), query):
                    fake.bad_signatures += 1
                    return self._send(
                        403, b"<Error><Code>SignatureDoesNotMatch</Code><Message>bad</Message></Error>"
                    )
                if u.path == "/flatfiles":
                    return self._list(dict(query))
                key = unquote(u.path[len("/flatfiles/") :])
                fault = fake.faults.get(key, [])
                f = fault.pop(0) if fault else None
                if f == "403":
                    return self._send(403, b"<Error><Code>AccessDenied</Code><Message>plan</Message></Error>")
                if f == "500":
                    return self._send(500, b"<Error><Code>InternalError</Code><Message>x</Message></Error>")
                if key not in fake.objects:
                    return self._send(404, b"<Error><Code>NoSuchKey</Code><Message>none</Message></Error>")
                body = fake.objects[key]
                etag = '"' + hashlib.md5(body).hexdigest() + '"'
                if f == "corrupt":
                    body = body[:-1] + bytes([body[-1] ^ 1])
                if f == "cut":
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("ETag", etag)
                    self.end_headers()
                    self.wfile.write(body[: len(body) // 2])
                    self.wfile.flush()
                    self.close_connection = True
                    return None
                return self._send(200, body, "application/octet-stream", {"ETag": etag})

            def _list(self, q: dict):
                prefix = q.get("prefix", "")
                delim = q.get("delimiter")
                keys = sorted(k for k in fake.objects if k.startswith(prefix))
                if delim:
                    cps = sorted(
                        {
                            prefix + k[len(prefix) :].split(delim, 1)[0] + delim
                            for k in keys
                            if delim in k[len(prefix) :]
                        }
                    )
                    keys = [k for k in keys if delim not in k[len(prefix) :]]
                else:
                    cps = []
                start = int(q.get("continuation-token", "0") or 0)
                page = keys[start : start + fake.page_size]
                nxt = start + fake.page_size
                more = nxt < len(keys)
                items = "".join(
                    f"<Contents><Key>{k}</Key><Size>{len(fake.objects[k])}</Size>"
                    f"<ETag>&quot;{hashlib.md5(fake.objects[k]).hexdigest()}&quot;</ETag>"
                    "<LastModified>2026-01-01T00:00:00.000Z</LastModified></Contents>"
                    for k in page
                )
                cp = "".join(f"<CommonPrefixes><Prefix>{p}</Prefix></CommonPrefixes>" for p in cps)
                body = (
                    '<?xml version="1.0" encoding="UTF-8"?>'
                    '<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
                    f"<Name>flatfiles</Name><Prefix>{prefix}</Prefix><KeyCount>{len(page)}</KeyCount>"
                    f"<IsTruncated>{'true' if more else 'false'}</IsTruncated>"
                    + (f"<NextContinuationToken>{nxt}</NextContinuationToken>" if more else "")
                    + items
                    + cp
                    + "</ListBucketResult>"
                ).encode()
                return self._send(200, body)

            def _api(self, u):
                q = dict(parse_qsl(u.query))
                if q.get("apiKey") != API_KEY:
                    return self._send(401, json.dumps({"message": "bad key"}).encode(), "application/json")
                rows = [
                    t
                    for t in fake.tickers
                    if str(t.get("active", True)).lower() == q.get("active", "true")
                    and ("ticker" not in q or t["ticker"] == q["ticker"])
                ]
                start = int(q.get("cursor", "0"))
                lim = int(q.get("limit", "1000"))
                page = rows[start : start + min(lim, 2)]
                out = {"results": page, "status": "OK"}
                if start + 2 < len(rows):
                    out["next_url"] = (
                        f"http://{self.headers['Host']}/v3/reference/tickers?cursor={start + 2}&active={q.get('active', 'true')}"
                    )
                return self._send(200, json.dumps(out).encode(), "application/json")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *a):
        self.server.shutdown()
        self.server.server_close()
