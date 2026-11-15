"""Quotes around sampled fills, for stage 2's quote check (standard library only; runs on the Mac).

For each row of ``data/stage2/quote_sample.csv`` (date, sym, entry_minute, close, ...): the NBBO
quotes from two minutes before the fill bar to the end of the minute after it, and from six to
four minutes before the session close (15:54-15:56, or 12:54-12:56 on early-close days). One JSON
line per window; re-running skips the windows already fetched.
"""

from __future__ import annotations

import csv
import datetime as _dt
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from qts_rebound.massive.rest import Rest

NY = ZoneInfo("America/New_York")
LIMIT = "50000"


def ns_at(date: str, minute: float) -> int:
    d = _dt.date.fromisoformat(date)
    m = int(minute)
    t = _dt.datetime(d.year, d.month, d.day, m // 60, m % 60, tzinfo=NY)
    return int(t.timestamp()) * 1_000_000_000


def windows(row: dict[str, str]) -> list[tuple[str, int, int]]:
    """(label, start ns, end ns) for one sampled fill. The fill bar is the minute before the entry
    time (entry = the end of the fill bar); an auction part enters at 09:30."""
    entry = float(row["entry_minute"])
    fill_bar = entry - 1 if entry > 570 else 570
    close = int(float(row.get("close") or 960))
    return [
        ("entry", ns_at(row["date"], fill_bar - 2), ns_at(row["date"], fill_bar + 2)),
        ("close", ns_at(row["date"], close - 6), ns_at(row["date"], close - 4)),
    ]


def fetch_sample(rest: Rest, sample_csv: Path, out_jsonl: Path, printer=print) -> int:
    done = set()
    if out_jsonl.exists():
        for line in out_jsonl.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            done.add((r["date"], r["sym"], r["label"]))
    with open(sample_csv, encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    n = 0
    out_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with open(out_jsonl, "a", encoding="utf-8") as f:
        for i, row in enumerate(rows, 1):
            for label, t0, t1 in windows(row):
                if (row["date"], row["sym"], label) in done:
                    continue
                quotes = list(
                    rest.paginate(
                        f"/v3/quotes/{row['sym']}",
                        {
                            "timestamp.gte": str(t0),
                            "timestamp.lt": str(t1),
                            "limit": LIMIT,
                            "order": "asc",
                            "sort": "timestamp",
                        },
                    )
                )
                keep = [
                    {k: q.get(k) for k in ("sip_timestamp", "bid_price", "bid_size", "ask_price", "ask_size")}
                    for q in quotes
                ]
                f.write(
                    json.dumps({"date": row["date"], "sym": row["sym"], "label": label, "quotes": keep})
                    + "\n"
                )
                f.flush()
                n += 1
            if i % 50 == 0:
                printer(f"  {i}/{len(rows)} fills")
    printer(f"  {n} windows fetched -> {out_jsonl}")
    return n


# ------------------------------------------------------------------ ticks around open-window fills

TICK_FIELDS = {
    "trades": ("sip_timestamp", "price", "size", "exchange", "conditions", "trf_id"),
    "quotes": (
        "sip_timestamp",
        "bid_price",
        "bid_size",
        "bid_exchange",
        "ask_price",
        "ask_size",
        "ask_exchange",
    ),
}


def tick_window(row: dict[str, str]) -> tuple[int, int]:
    """From 09:28 (or two minutes before the fill bar, if later) to three minutes after it."""
    entry = float(row["entry_minute"])
    fill_bar = entry - 1 if entry > 570 else 570
    return ns_at(row["date"], max(568, fill_bar - 2)), ns_at(row["date"], fill_bar + 4)


def fetch_ticks(rest: Rest, sample_csv: Path, out_jsonl: Path, printer=print) -> int:
    """Trades and NBBO quotes around each sampled fill (exploratory tick check): one JSON line
    per fill with both lists; re-running skips the fills already fetched."""
    done = set()
    if out_jsonl.exists():
        for line in out_jsonl.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            done.add((r["date"], r["sym"]))
    with open(sample_csv, encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    n = 0
    out_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with open(out_jsonl, "a", encoding="utf-8") as f:
        for i, row in enumerate(rows, 1):
            if (row["date"], row["sym"]) in done:
                continue
            t0, t1 = tick_window(row)
            rec = {"date": row["date"], "sym": row["sym"], "t0": t0, "t1": t1}
            for kind, fields in TICK_FIELDS.items():
                items = rest.paginate(
                    f"/v3/{kind}/{row['sym']}",
                    {
                        "timestamp.gte": str(t0),
                        "timestamp.lt": str(t1),
                        "limit": LIMIT,
                        "order": "asc",
                        "sort": "timestamp",
                    },
                )
                rec[kind] = [{k: x.get(k) for k in fields} for x in items]
            f.write(json.dumps(rec) + "\n")
            f.flush()
            n += 1
            if i % 25 == 0:
                printer(f"  {i}/{len(rows)} fills")
    printer(f"  {n} fills fetched -> {out_jsonl}")
    return n
