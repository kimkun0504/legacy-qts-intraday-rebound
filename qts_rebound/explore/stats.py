"""Summaries for the exploratory search: discovery (to 2016) and validation (2017 on)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from qts_rebound.explore import engine
from qts_rebound.stage1 import nw_t

SPLIT = pd.Timestamp("2017-01-01")
STRESS_RT = 0.01  # extra round-trip cost in the stressed net


def load(panel: Path) -> pd.DataFrame:
    t = pd.concat([pd.read_parquet(p) for p in sorted(panel.glob("*.parquet"))], ignore_index=True)
    t["date"] = pd.to_datetime(t["date"])
    return t


def summary(t: pd.DataFrame, col: str, days: pd.DatetimeIndex) -> dict:
    """Gross = the return column; net = gross - cost. t on the daily sum of net (NW 5)."""
    if len(t) == 0:
        return {"n": 0}
    g = t[col].to_numpy(dtype=float)
    net = g - t["cost"].to_numpy(dtype=float)
    s = pd.Series(net).groupby(t["date"].to_numpy()).sum().reindex(days, fill_value=0.0)
    return {
        "n": len(t),
        "per_day": len(t) / max(len(days), 1),
        "gross": float(g.mean()),
        "median": float(np.median(g)),
        "wins": float((g > 0).mean()),
        "net": float(net.mean()),
        "net_w": float(np.clip(net, -0.5, 0.5).mean()),
        "net_stress": float(net.mean() - STRESS_RT),
        "t": float(nw_t(s.to_numpy(), 5)[1]),
    }


def both(t: pd.DataFrame, col: str, days: pd.DatetimeIndex) -> dict:
    d = summary(t[t["date"] < SPLIT], col, days[days < SPLIT])
    v = summary(t[t["date"] >= SPLIT], col, days[days >= SPLIT])
    return {**{f"d_{k}": x for k, x in d.items()}, **{f"v_{k}": x for k, x in v.items()}}


def names() -> tuple[list[str], list[str]]:
    return [engine.entry_name(e) for e in engine.ENTRIES], [engine.exit_name(x) for x in engine.EXITS]
