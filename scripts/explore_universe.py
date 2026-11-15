"""The opening-sweep rule in another universe (small or large caps), as an out-of-universe check.

The rule was shaped on micro caps. Small and large caps were not looked at, so the same rule
there is new information (same years, different stocks). Research window only.

    python scripts/explore_universe.py small map
    python scripts/explore_universe.py small extract [--budget S]
    python scripts/explore_universe.py small build
    python scripts/explore_universe.py small eval

Only stock-days whose low reached the 5 % floor limit are mapped and extracted.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

import explore as ex  # noqa: E402
import run_stage2 as s2  # noqa: E402

from qts_rebound.explore import engine, stats  # noqa: E402
from qts_rebound.stage2.data import WINDOW_START, last_research_day, research_files  # noqa: E402
from qts_rebound.stage2.extract import extract_files, months  # noqa: E402
from qts_rebound.stage2.fills import orders_table  # noqa: E402
from qts_rebound.stage2.mapping import alias_table, match  # noqa: E402

FLOOR = 0.05
ENTRIES = [
    ("lim", 2, 0, 0.05, 575),
    ("lim", 2, 0, 0.08, 575),
    ("lim", 2, 0.05, 0.05, 575),
    ("vol", 2, 0, 0.05),
    ("lim", 2, 0, 0.05),
]
RULES = {  # name: (entry, cancel filter minute, pre-market cap, exit)
    "open_sweep_5": ("lim2_abs5_c575", 960, 0.01, "moc"),
    "open_sweep_5_low_exit": ("lim2_abs5_c575", 960, 0.01, "timelow60"),
    "open_sweep_8": ("lim2_abs8_c575", 960, 0.01, "moc"),
    "open_sweep_5_strict5": ("lim2_0.05_abs5_c575", 960, 0.01, "moc"),
    "open_sweep_5_reactive": ("vol2_0_abs5", 576, 0.01, "moc"),
    "open_sweep_5_any_premarket": ("lim2_abs5_c575", 960, 1e9, "moc"),
    "limit_5_all_day": ("lim2_abs5", 960, 1e9, "moc"),
}


def out_dir(u: str) -> Path:
    return ex.OUT / u


def candidates(u: str) -> pd.DataFrame:
    c = pd.read_parquet(s2.OUT / f"candidates_{u}.parquet", columns=s2.CAND_COLS)
    c["date"] = pd.to_datetime(c["date"])
    c = c[(c["date"] >= pd.Timestamp(WINDOW_START)) & (c["date"] <= pd.Timestamp(last_research_day()))]
    reach = c["lo"] <= c["c_prev"] * (1 - np.maximum(2 * c["sigma"], FLOOR))
    return c[reach].copy()


def cmd_map(u: str, a) -> None:
    cands = candidates(u)
    sm = pd.read_parquet(s2.SHARED_STORE / "security_master", columns=["ticker", "relatedtickers"])
    alias = alias_table(sm)
    files = research_files("day_aggs")
    out = []
    for y, cy in cands.groupby(cands["date"].dt.year):
        fy = [p for d, p in files if d.year == y]
        if fy:
            out.append(match(cy, alias, fy))
    mp = pd.concat(out, ignore_index=True)
    out_dir(u).mkdir(parents=True, exist_ok=True)
    mp.to_parquet(out_dir(u) / "mapping.parquet", index=False)
    print(
        f"{u}: {len(cands):,} stock-days reached the limit, {len(mp):,} matched ({len(mp) / len(cands):.1%})"
    )


def cmd_extract(u: str, a) -> None:
    mp = pd.read_parquet(out_dir(u) / "mapping.parquet")
    wanted = mp[["date", "sym"]].copy()
    wanted["date"] = pd.to_datetime(wanted["date"])
    wanted["month"] = wanted["date"].dt.strftime("%Y-%m")
    mdir = out_dir(u) / "minute"
    mdir.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    for month, fl in months(research_files("minute_aggs")).items():
        f = mdir / f"{month}.parquet"
        if f.exists():
            continue
        if a.budget and time.monotonic() - t0 > a.budget:
            print("budget reached; run again")
            return
        n = extract_files(fl, wanted[wanted["month"] == month], f)
        print(f"  {month}: {n:,} bars ({time.monotonic() - t0:.0f}s)", flush=True)
    print("extract: done")


def cmd_build(u: str, a) -> None:
    o_all = orders_table(candidates(u), pd.read_parquet(out_dir(u) / "mapping.parquet"))
    o_all["month"] = o_all["date"].dt.strftime("%Y-%m")
    names = [engine.entry_name(e) for e in ENTRIES]
    parts = []
    for month in sorted(o_all["month"].unique()):
        if not (out_dir(u) / "minute" / f"{month}.parquet").exists():
            continue
        t = ex.build_month(
            month,
            o_all[o_all["month"] == month].drop(columns=["month"]),
            entries=ENTRIES,
            minutes=out_dir(u) / "minute",
        )
        t["entry"] = [names[i] for i in t["e"]]
        parts.append(t)
    pd.concat(parts, ignore_index=True).to_parquet(out_dir(u) / "panel.parquet", index=False)
    print(f"{u}: {sum(map(len, parts)):,} rows")


def cmd_eval(u: str, a) -> None:
    t = pd.read_parquet(out_dir(u) / "panel.parquet")
    t["date"] = pd.to_datetime(t["date"])
    t = t[~t["news"]]
    days = s2.trading_days()
    _, xn = stats.names()
    res = {}
    for name, (entry, w, pm, xname) in RULES.items():
        x = t[(t["entry"] == entry) & (t["emin"] <= w) & (t["pre_dv"] < pm * t["adv_prev"])]
        col = f"r{xn.index(xname)}"
        net = x[col] - x["cost"]
        res[name] = {
            "trial": f"{u}:{name}",
            "periods": ex._periods(x, col, days),
            "by_year_net": {
                str(k): round(float(v), 5) for k, v in net.groupby(x["date"].dt.year).mean().items()
            },
        }
        print(
            name,
            {
                k: (v.get("n"), round(v.get("net", 0) * 100, 2), round(v.get("nw_t", 0), 1))
                for k, v in res[name]["periods"].items()
            },
        )
    (ex.OUT / f"results_open_sweep_{u}.json").write_text(json.dumps({"universe": u, "rules": res}, indent=1))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("universe", choices=["small", "large"])
    ap.add_argument("cmd", choices=["map", "extract", "build", "eval"])
    ap.add_argument("--budget", type=float, default=0)
    a = ap.parse_args()
    {"map": cmd_map, "extract": cmd_extract, "build": cmd_build, "eval": cmd_eval}[a.cmd](a.universe, a)


if __name__ == "__main__":
    main()
