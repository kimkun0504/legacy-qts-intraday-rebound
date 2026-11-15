#!/usr/bin/env python3
"""Download Massive flat files and reference data into data/massive (runs on the Mac).

Standard library only: any Python 3.9+ works, nothing to install. Keys come from .env.

    python3 scripts/massive.py check      # test the keys with two small downloads (~10 MB)
    python3 scripts/massive.py plan       # list every file; sizes and free disk space
    python3 scripts/massive.py get        # download everything (resumable: re-run to continue)
    python3 scripts/massive.py verify     # re-check downloaded files against Massive's checksums
    python3 scripts/massive.py tickers    # ticker reference lists, active and delisted
    python3 scripts/massive.py quotes     # quotes around the 500 sampled stage-2 fills (later)

Options for plan/get/verify: --datasets day_aggs,minute_aggs  --from 2003-09-10  --to 2026-11-13
--workers 4  --time-budget SECONDS  --refresh (re-list instead of using today's listing).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import gzip
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from qts_rebound.massive import env  # noqa: E402
from qts_rebound.massive.flatfiles import (  # noqa: E402
    DATASETS,
    FIRST_DATE,
    download_all,
    fetch_one,
    free_bytes,
    key_date,
    list_dataset,
    load_listing,
    local_path,
    read_holdout_start,
    save_listing,
    verify_local,
)
from qts_rebound.massive.quotes import fetch_sample  # noqa: E402
from qts_rebound.massive.rest import API, Rest, download_tickers  # noqa: E402
from qts_rebound.massive.s3 import ENDPOINT, AuthError, S3Client, S3Error  # noqa: E402

SEAL_FILE = REPO_ROOT / "research" / "seal.yaml"
DEFAULT_ROOT = REPO_ROOT / "data" / "massive"
LISTING_MAX_AGE_H = 24.0


def s3_client() -> S3Client:
    return S3Client(
        env.require(env.S3_ACCESS),
        env.require(env.S3_SECRET),
        endpoint=os.environ.get("QTS_MASSIVE_ENDPOINT", ENDPOINT),
    )


def rest_client() -> Rest:
    return Rest(env.require(env.API_KEY), base=os.environ.get("QTS_MASSIVE_API", API))


def parse_date(s: str) -> _dt.date:
    return _dt.date.fromisoformat(s)


def listing(client: S3Client, root: Path, dataset: str, start, end, refresh: bool):
    path = root / "manifest" / f"listing_{dataset}.csv"
    fresh = path.exists() and (time.time() - path.stat().st_mtime) < LISTING_MAX_AGE_H * 3600
    if fresh and not refresh:
        objs = load_listing(path)
        src = "today's listing"
    else:
        print(f"Listing {dataset} month by month ...", flush=True)
        objs = list_dataset(client, dataset, FIRST_DATE, _dt.date.today())
        save_listing(path, objs)
        src = "fresh listing"
    sel = [o for o in objs if start <= key_date(o.key) <= end]
    return sel, src


def cmd_check(a) -> int:
    root = Path(a.root)
    hs = read_holdout_start(SEAL_FILE)
    ok = True
    print("1/5 keys in .env")
    missing = [n for n in (env.S3_ACCESS, env.S3_SECRET, env.API_KEY) if env.get(n) is None]
    if missing:
        print("    MISSING:", ", ".join(missing), f"-> edit {env.ENV_FILE}")
        return 1
    print("    OK (values not shown)")
    c = s3_client()
    print("2/5 flat-file listing")
    try:
        _, prefixes = c.list("us_stocks_sip/", delimiter="/")
        names = sorted(p.rstrip("/").split("/")[-1] for p in prefixes)
        print("    OK: datasets visible:", ", ".join(names) or "(none)")
    except (AuthError, S3Error) as e:
        print("    FAILED:", e, "-> check the S3 Access Key and Secret Key (not the API key)")
        return 1
    for step, dataset in (("3/5", "day_aggs"), ("4/5", "minute_aggs")):
        key = f"{DATASETS[dataset]}/{FIRST_DATE:%Y/%m}/{FIRST_DATE.isoformat()}.csv.gz"
        print(f"{step} download test: {dataset} for {FIRST_DATE} (the first day Massive has)")
        try:
            objs, _ = c.list(key)
            if not objs:
                print("    FAILED: file not listed")
                ok = False
                continue
            dest = local_path(root, key, hs)
            out = fetch_one(c, objs[0], dest)
            if out.status not in ("done", "skipped"):
                print(f"    FAILED: {out.status} {out.error}")
                ok = False
                continue
            with gzip.open(dest, "rt", encoding="utf-8") as f:
                header = f.readline().strip()
                tickers, rows = set(), 0
                for line in f:
                    rows += 1
                    tickers.add(line.split(",", 1)[0])
            check = out.check or "already downloaded"
            print(
                f"    OK: {dest.stat().st_size / 1e6:.1f} MB ({check}), {rows:,} rows, {len(tickers):,} tickers; "
                f"columns: {header}"
            )
            if dataset == "minute_aggs":
                print(
                    "    delisted names present (LEH = Lehman Brothers, delisted 2008):",
                    "yes" if "LEH" in tickers else "no",
                )
        except AuthError as e:
            print("    FAILED:", e)
            print("    -> the keys work for listing but the plan does not include this file.")
            print("       2003 minute bars need Stocks Advanced (all history); check the subscription.")
            ok = False
        except S3Error as e:
            print("    FAILED:", e)
            ok = False
    print("5/5 API key: reference lookup of LEH among delisted tickers")
    try:
        res = rest_client().get_json(
            "/v3/reference/tickers", {"ticker": "LEH", "market": "stocks", "active": "false"}
        )
        r = (res.get("results") or [{}])[0]
        print(f"    OK: {r.get('ticker', '?')} {r.get('name', '')!s} delisted {r.get('delisted_utc', '?')}")
    except (AuthError, S3Error) as e:
        print("    FAILED:", e, "-> check MASSIVE_API_KEY")
        ok = False
    print("\nAll checks passed. Next: python3 scripts/massive.py plan" if ok else "\nSome checks failed.")
    return 0 if ok else 1


def _datasets(a):
    names = [d.strip() for d in a.datasets.split(",") if d.strip()]
    bad = [d for d in names if d not in DATASETS]
    if bad:
        raise SystemExit(f"unknown dataset(s): {bad}; choose from {sorted(DATASETS)}")
    return names


def cmd_plan(a) -> int:
    root = Path(a.root)
    hs = read_holdout_start(SEAL_FILE)
    c = s3_client()
    total_left = 0
    for ds in _datasets(a):
        objs, src = listing(c, root, ds, a.from_, a.to, a.refresh)
        res = [o for o in objs if key_date(o.key) < hs]
        sea = [o for o in objs if key_date(o.key) >= hs]
        have = [o for o in objs if _have(root, o, hs)]
        left = sum(o.size for o in objs) - sum(o.size for o in have)
        total_left += left
        first = key_date(objs[0].key) if objs else None
        last = key_date(objs[-1].key) if objs else None
        print(
            f"{ds} ({src}): {len(objs):,} files, {sum(o.size for o in objs) / 1e9:,.1f} GB, {first} .. {last}"
        )
        print(
            f"    research (before {hs}): {len(res):,} files, {sum(o.size for o in res) / 1e9:,.1f} GB"
            f" | holdout, kept sealed: {len(sea):,} files, {sum(o.size for o in sea) / 1e9:,.1f} GB"
        )
        print(f"    already here: {len(have):,} files | still to download: {left / 1e9:,.1f} GB")
    free = free_bytes(root)
    verdict = "enough" if free > _need(total_left) else "NOT ENOUGH"
    print(f"Free disk where the files go ({root}): {free / 1e9:,.0f} GB -> {verdict}")
    return 0


def _need(left: int) -> float:
    """Bytes of free space wanted before downloading ``left`` bytes: 5 % plus 10 GB of headroom."""
    return left * 1.05 + (10e9 if left > 1e9 else 0)


def _have(root: Path, o, hs) -> bool:
    p = local_path(root, o.key, hs)
    return p.exists() and p.stat().st_size == o.size


def cmd_get(a) -> int:
    root = Path(a.root)
    hs = read_holdout_start(SEAL_FILE)
    c = s3_client()
    plans = [(ds, listing(c, root, ds, a.from_, a.to, a.refresh)[0]) for ds in _datasets(a)]
    left = sum(o.size for _, objs in plans for o in objs if not _have(root, o, hs))
    free = free_bytes(root)
    if free < _need(left) and not a.force:
        print(
            f"Not enough disk: {left / 1e9:,.1f} GB to download, {free / 1e9:,.1f} GB free. Use --force to try anyway."
        )
        return 1
    t0 = time.monotonic()
    budget = a.time_budget
    for ds, objs in plans:
        spent = time.monotonic() - t0
        if budget and spent >= budget:
            break
        print(f"{ds}: {len(objs):,} files listed for {a.from_} .. {a.to}", flush=True)
        counts = download_all(
            c,
            objs,
            root,
            hs,
            workers=a.workers,
            time_budget=(budget - spent) if budget else None,
            log_path=root / "manifest" / "downloads.jsonl",
        )
        print(
            f"    downloaded {counts['done']:,}, already here {counts['skipped']:,}, "
            f"not on the server {counts['missing']:,}, failed {counts['failed']:,}, not started {counts['stopped']:,}"
        )
        if counts["failed"] or counts["stopped"]:
            print("    Re-run the same command to retry or continue; finished files are kept.")
    print(f"Done in {_dt.timedelta(seconds=int(time.monotonic() - t0))}.")
    return 0


def cmd_verify(a) -> int:
    root = Path(a.root)
    hs = read_holdout_start(SEAL_FILE)
    bad_total = 0
    for ds in _datasets(a):
        path = root / "manifest" / f"listing_{ds}.csv"
        if not path.exists():
            print(f"{ds}: no listing yet; run plan or get first")
            continue
        objs = [o for o in load_listing(path) if a.from_ <= key_date(o.key) <= a.to]
        print(f"{ds}: checking {len(objs):,} files ...", flush=True)
        res = verify_local(objs, root, hs, workers=a.workers)
        bad = res["size"] + res["checksum"]
        bad_total += len(bad) + len(res["missing"])
        print(
            f"    ok {len(res['ok']):,} | missing {len(res['missing']):,} | wrong size {len(res['size']):,}"
            f" | wrong checksum {len(res['checksum']):,}"
        )
        for k in bad[:20]:
            print("    bad:", k)
        if a.fix:
            for k in res["checksum"]:
                p = local_path(root, k, hs)
                p.rename(p.with_name(p.name + ".bad"))
            if res["checksum"]:
                print(
                    f"    renamed {len(res['checksum'])} bad file(s) to *.bad; run get to download them again"
                )
    return 0 if bad_total == 0 else 1


def cmd_tickers(a) -> int:
    download_tickers(rest_client(), Path(a.root) / "reference")
    return 0


def cmd_quotes(a) -> int:
    sample = Path(a.sample) if a.sample else REPO_ROOT / "data" / "stage2" / "quote_sample.csv"
    if not sample.exists():
        print(f"No sample yet ({sample}): it is written by the stage-2 run (run_stage2.py quote-sample).")
        return 1
    fetch_sample(rest_client(), sample, Path(a.root) / "quotes" / "quote_check.jsonl")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--root", default=os.environ.get("QTS_MASSIVE_ROOT", str(DEFAULT_ROOT)))
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    sub.add_parser("tickers")
    q = sub.add_parser("quotes")
    q.add_argument("--sample", default=None)
    for name in ("plan", "get", "verify"):
        s = sub.add_parser(name)
        s.add_argument("--datasets", default="day_aggs,minute_aggs")
        s.add_argument("--from", dest="from_", type=parse_date, default=FIRST_DATE)
        s.add_argument("--to", type=parse_date, default=_dt.date.today())
        s.add_argument("--workers", type=int, default=4)
        s.add_argument("--refresh", action="store_true")
        s.add_argument("--time-budget", type=float, default=None)
        s.add_argument("--force", action="store_true")
        s.add_argument("--fix", action="store_true")
    a = p.parse_args(argv)
    cmds = {
        "check": cmd_check,
        "plan": cmd_plan,
        "get": cmd_get,
        "verify": cmd_verify,
        "tickers": cmd_tickers,
    }
    try:
        return cmds[a.cmd](a)
    except env.MissingCredential as e:
        print(e)
        return 1
    except AuthError as e:
        print(f"Access refused by Massive: {e}. Check the keys in .env and the plan (Stocks Advanced).")
        return 1
    except KeyboardInterrupt:
        print("Stopped. Finished files are kept; run the same command again to continue.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
