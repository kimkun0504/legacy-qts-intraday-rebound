"""Tick check for the opening-window fills: were the prints through the limit real, on-exchange
trades that a resting bid would have met, and what did the quote look like around them?

Input: ``data/massive/quotes/tick_check.jsonl`` (scripts/massive.py ticks) and the sample CSV.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

TRF = 4  # FINRA trade reporting facility: off-exchange prints
NS = 1_000_000_000


def check_fill(rec: dict, limit: float, shares: float) -> dict:
    tr = pd.DataFrame(
        rec.get("trades") or [],
        columns=["sip_timestamp", "price", "size", "exchange", "conditions", "trf_id"],
    )
    qu = pd.DataFrame(
        rec.get("quotes") or [], columns=["sip_timestamp", "bid_price", "bid_size", "ask_price", "ask_size"]
    )
    out = {"date": rec["date"], "sym": rec["sym"], "n_trades": len(tr), "n_quotes": len(qu)}
    at = tr[tr["price"] <= limit]
    out["prints_at_limit"] = len(at)
    if len(at) == 0:
        return out
    lit = at[at["exchange"] != TRF]
    out["shares_at_limit"] = float(at["size"].sum())
    out["lit_shares_at_limit"] = float(lit["size"].sum())
    out["lit_round_lot_prints"] = int((lit["size"] >= 100).sum())
    out["lit_covers_order"] = bool(lit["size"].sum() >= shares)
    out["low_print"] = float(at["price"].min())
    t0 = int(at["sip_timestamp"].iloc[0])
    out["first_print_s"] = (t0 - rec["t0"]) / NS  # seconds after the window start
    before = qu[qu["sip_timestamp"] < t0]
    if len(before):
        b = before.iloc[-1]
        out["bid_before"], out["ask_before"] = float(b["bid_price"]), float(b["ask_price"])
        if b["bid_price"] > 0 and b["ask_price"] > 0:
            out["spread_before"] = float(b["ask_price"] / b["bid_price"] - 1)
        out["bid_above_limit_before"] = bool(b["bid_price"] > limit)
    for lag in (1, 10, 60):
        after = qu[(qu["sip_timestamp"] >= t0) & (qu["sip_timestamp"] <= t0 + lag * NS)]
        if len(after):
            out[f"ask_{lag}s_vs_limit"] = float(after.iloc[-1]["ask_price"] / limit - 1)
    return out


def check(sample_csv: Path, ticks_jsonl: Path) -> pd.DataFrame:
    s = pd.read_csv(sample_csv, dtype={"date": str})
    key = {(r.date, r.sym): r for r in s.itertuples()}
    rows = []
    for line in ticks_jsonl.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        r = key.get((rec["date"], rec["sym"]))
        if r is not None:
            rows.append(
                {
                    **check_fill(rec, float(r.limit), float(r.shares)),
                    "year": int(rec["date"][:4]),
                    "net": r.net,
                }
            )
    return pd.DataFrame(rows)


def summary(c: pd.DataFrame) -> dict:
    has = c[c["prints_at_limit"] > 0]

    def share(col):
        return float(has[col].fillna(False).astype(bool).mean()) if len(has) and col in has else float("nan")

    def med(col):
        return float(np.nanmedian(has[col])) if len(has) and col in has else float("nan")

    return {
        "fills_checked": len(c),
        "with_a_print_at_or_below_the_limit": float((c["prints_at_limit"] > 0).mean())
        if len(c)
        else float("nan"),
        "lit_exchange_volume_covers_the_order": share("lit_covers_order"),
        "bid_was_above_the_limit_just_before": share("bid_above_limit_before"),
        "median_spread_before": med("spread_before"),
        "median_ask_vs_limit_1s": med("ask_1s_vs_limit"),
        "median_ask_vs_limit_10s": med("ask_10s_vs_limit"),
        "median_ask_vs_limit_60s": med("ask_60s_vs_limit"),
    }
