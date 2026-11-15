"""Mirror Massive's daily flat files into ``data/massive``, resumably.

Layout (mirrors the S3 keys)::

    data/massive/raw/us_stocks_sip/minute_aggs_v1/2010/01/2010-01-04.csv.gz     research dates
    data/massive/sealed/us_stocks_sip/minute_aggs_v1/2024/01/2024-01-02.csv.gz  holdout dates
    data/massive/manifest/listing_<dataset>.csv   what Massive lists (key, size, etag)
    data/massive/manifest/downloads.jsonl         one line per verified download

Files dated on or after the seal's ``holdout_start`` go under ``sealed/``: they are downloaded
(the subscription is paid by the month) but no research code reads that folder until the owner
lifts the seal. A file counts as done when its size matches the listing; every download is
checked against Massive's MD5 (the ETag) or, when the ETag is not an MD5, by decompressing it.
"""

from __future__ import annotations

import datetime as _dt
import gzip
import json
import os
import random
import re
import shutil
import threading
import time
import zlib
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from qts_rebound.massive.s3 import AuthError, NotFound, Retryable, S3Client, S3Error, S3Object

DATASETS: dict[str, str] = {
    "day_aggs": "us_stocks_sip/day_aggs_v1",
    "minute_aggs": "us_stocks_sip/minute_aggs_v1",
}
FIRST_DATE = _dt.date(2003, 9, 10)  # first file Massive publishes for US stocks
KEY_RE = re.compile(r"/(\d{4})/(\d{2})/(\d{4}-\d{2}-\d{2})\.csv\.gz$")
MD5_RE = re.compile(r"^[0-9a-f]{32}$")


def key_date(key: str) -> _dt.date | None:
    m = KEY_RE.search(key)
    return _dt.date.fromisoformat(m.group(3)) if m else None


def read_holdout_start(seal_file: Path) -> _dt.date:
    m = re.search(r"^holdout_start:\s*['\"]?(\d{4}-\d{2}-\d{2})", seal_file.read_text(), re.MULTILINE)
    if not m:
        raise ValueError(f"no holdout_start in {seal_file}")
    return _dt.date.fromisoformat(m.group(1))


def local_path(root: Path, key: str, holdout_start: _dt.date) -> Path:
    d = key_date(key)
    sealed = d is not None and d >= holdout_start
    return root / ("sealed" if sealed else "raw") / key


def month_prefixes(dataset_prefix: str, start: _dt.date, end: _dt.date) -> list[str]:
    out = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append(f"{dataset_prefix}/{y:04d}/{m:02d}/")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


# ---------------------------------------------------------------- listings


def list_dataset(
    client: S3Client, dataset: str, start: _dt.date, end: _dt.date, workers: int = 8
) -> list[S3Object]:
    """Every file of ``dataset`` dated in [start, end], one listing request per month."""
    prefixes = month_prefixes(DATASETS[dataset], start, end)
    out: list[S3Object] = []

    def one(p: str) -> list[S3Object]:
        return retry(lambda: client.list(p)[0], what=f"listing {p}")

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for objs in ex.map(one, prefixes):
            out += [o for o in objs if (d := key_date(o.key)) is not None and start <= d <= end]
    return sorted(out, key=lambda o: o.key)


def save_listing(path: Path, objs: Sequence[S3Object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("key,size,etag,last_modified\n")
        for o in objs:
            f.write(f"{o.key},{o.size},{o.etag},{o.last_modified}\n")
    os.replace(tmp, path)


def load_listing(path: Path) -> list[S3Object]:
    out = []
    with open(path, encoding="utf-8") as f:
        next(f)
        for line in f:
            k, s, e, lm = line.rstrip("\n").split(",", 3)
            out.append(S3Object(k, int(s), e, lm))
    return out


# ---------------------------------------------------------------- retries


def retry(fn: Callable, what: str, attempts: int = 6, sleep: Callable[[float], None] = time.sleep):
    """Retries Retryable errors with exponential backoff and jitter (2, 4, 8, 16, 32 s ...)."""
    for i in range(attempts):
        try:
            return fn()
        except Retryable as e:
            if i == attempts - 1:
                raise Retryable(f"{what}: gave up after {attempts} tries ({e})") from None
            sleep(min(60.0, 2.0 ** (i + 1)) * (0.5 + random.random()))
    raise AssertionError("unreachable")


# ---------------------------------------------------------------- downloads


def gzip_ok(path: Path) -> bool:
    """True if the file decompresses to the end with a valid CRC (gzip's own check)."""
    try:
        with gzip.open(path, "rb") as f:
            while f.read(1 << 22):
                pass
        return True
    except (OSError, EOFError, zlib.error):
        return False


@dataclass
class Outcome:
    key: str
    status: str  # done | skipped | missing | failed | stopped
    bytes: int = 0
    check: str = ""
    error: str = ""


def fetch_one(
    client: S3Client,
    obj: S3Object,
    dest: Path,
    attempts: int = 6,
    sleep: Callable[[float], None] = time.sleep,
) -> Outcome:
    if dest.exists() and dest.stat().st_size == obj.size:
        return Outcome(obj.key, "skipped", 0)
    part = dest.with_name(dest.name + ".part")

    def attempt() -> Outcome:
        n, md5, etag = client.download(obj.key, part)
        if n != obj.size:
            raise Retryable(f"size {n} differs from the listing's {obj.size}")
        etag = etag or obj.etag
        if MD5_RE.match(etag):
            if md5 != etag:
                raise Retryable("MD5 differs from Massive's checksum")
            check = "md5"
        else:
            if not gzip_ok(part):
                raise Retryable("file does not decompress")
            check = "gzip"
        os.replace(part, dest)
        return Outcome(obj.key, "done", n, check)

    try:
        return retry(attempt, what=obj.key, attempts=attempts, sleep=sleep)
    except NotFound as e:
        return Outcome(obj.key, "missing", error=str(e))
    except AuthError:
        raise
    except S3Error as e:
        return Outcome(obj.key, "failed", error=str(e))


@dataclass
class Progress:
    total_files: int
    total_bytes: int
    every: float = 15.0
    printer: Callable[[str], None] = print
    files: int = 0
    nbytes: int = 0
    started: float = field(default_factory=time.monotonic)
    _last: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def add(self, o: Outcome, size: int) -> None:
        with self._lock:
            self.files += 1
            self.nbytes += size if o.status in ("done", "skipped") else 0
            now = time.monotonic()
            if now - self._last >= self.every or self.files == self.total_files:
                self._last = now
                self.printer(self.line(now))

    def line(self, now: float | None = None) -> str:
        el = max((now or time.monotonic()) - self.started, 1e-9)
        rate = self.nbytes / el
        left = max(self.total_bytes - self.nbytes, 0)
        eta = left / rate if rate > 0 else float("inf")
        eta_s = "--" if eta == float("inf") else str(_dt.timedelta(seconds=int(eta)))
        return (
            f"  {self.files:,}/{self.total_files:,} files | {self.nbytes / 1e9:,.2f}/{self.total_bytes / 1e9:,.2f} GB"
            f" | {rate / 1e6:,.1f} MB/s | about {eta_s} left"
        )


def download_all(
    client: S3Client,
    objs: Iterable[S3Object],
    root: Path,
    holdout_start: _dt.date,
    workers: int = 4,
    time_budget: float | None = None,
    printer: Callable[[str], None] = print,
    log_path: Path | None = None,
) -> dict[str, int]:
    """Downloads every listed file not yet on disk. Safe to stop (Ctrl-C) and re-run."""
    objs = list(objs)
    todo = [o for o in objs if not _is_done(local_path(root, o.key, holdout_start), o)]
    counts = {"done": 0, "skipped": len(objs) - len(todo), "missing": 0, "failed": 0, "stopped": 0}
    if not todo:
        printer("  nothing to download: every listed file is already here")
        return counts
    stop = threading.Event()
    log_lock = threading.Lock()
    prog = Progress(len(todo), sum(o.size for o in todo), printer=printer)
    deadline = time.monotonic() + time_budget if time_budget else None
    auth_error: list[str] = []

    def work(o: S3Object) -> Outcome:
        if stop.is_set() or (deadline and time.monotonic() > deadline):
            return Outcome(o.key, "stopped")
        try:
            out = fetch_one(client, o, local_path(root, o.key, holdout_start))
        except AuthError as e:
            stop.set()
            auth_error.append(str(e))
            return Outcome(o.key, "failed", error=str(e))
        if out.status == "done" and log_path is not None:
            rec = {
                "key": o.key,
                "bytes": out.bytes,
                "check": out.check,
                "etag": o.etag,
                "at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
            }
            with log_lock, open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")
        return out

    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
    problems: list[Outcome] = []
    ex = ThreadPoolExecutor(max_workers=workers)
    futs = {ex.submit(work, o): o for o in todo}
    try:
        for fut in as_completed(futs):
            o = futs[fut]
            out = fut.result()
            counts[out.status] = counts.get(out.status, 0) + 1
            if out.status in ("missing", "failed"):
                problems.append(out)
            prog.add(out, o.size)
    except KeyboardInterrupt:
        stop.set()
        printer("\n  stopping: letting the files in flight finish (Ctrl-C again to abort them)")
        for f in futs:
            f.cancel()
        ex.shutdown(wait=True)
        counts["stopped"] = sum(1 for f in futs if f.cancelled())
        raise
    finally:
        ex.shutdown(wait=True)
    if auth_error:
        raise AuthError(auth_error[0])
    for p in problems[:20]:
        printer(f"  {p.status}: {p.key} ({p.error})")
    return counts


def _is_done(dest: Path, o: S3Object) -> bool:
    return dest.exists() and dest.stat().st_size == o.size


def local_md5(path: Path) -> str:
    import hashlib

    h = hashlib.md5(usedforsecurity=False)
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 22), b""):
            h.update(b)
    return h.hexdigest()


def verify_local(
    objs: Iterable[S3Object], root: Path, holdout_start: _dt.date, workers: int = 4
) -> dict[str, list[str]]:
    """Re-checks files on disk against the listing: missing, wrong size, wrong MD5 (or not gzip)."""
    res: dict[str, list[str]] = {"ok": [], "missing": [], "size": [], "checksum": []}

    def one(o: S3Object) -> tuple[str, str]:
        p = local_path(root, o.key, holdout_start)
        if not p.exists():
            return "missing", o.key
        if p.stat().st_size != o.size:
            return "size", o.key
        good = local_md5(p) == o.etag if MD5_RE.match(o.etag) else gzip_ok(p)
        return ("ok" if good else "checksum"), o.key

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for status, key in ex.map(one, list(objs)):
            res[status].append(key)
    return res


def free_bytes(path: Path) -> int:
    p = path
    while not p.exists():
        p = p.parent
    return shutil.disk_usage(p).free
