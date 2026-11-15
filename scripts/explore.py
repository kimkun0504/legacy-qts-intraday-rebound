"""Exploratory entry/exit search on the stage-2 minute bars (research window only).

    python scripts/explore.py build [--budget S]   # per-month panel: one row per order x entry
    python scripts/explore.py scan                 # every entry x exit, plus the opening-window family
    python scripts/explore.py tick-sample          # 400 opening fills for scripts/massive.py ticks (Mac)
    python scripts/explore.py tick-check           # after the ticks are fetched: were the prints real?

Not a registered trial. The seal is respected through stage 2's own inputs (extracted months
end at the last research day). Results go to data/explore/.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

import run_stage2 as s2  # noqa: E402

from qts_rebound.explore import engine, stats  # noqa: E402
from qts_rebound.stage2.data import CLOSE, OPEN, PRE_LAST, PRE_OPEN  # noqa: E402
from qts_rebound.stage2.evaluate import costs, premarket_dollar_volume  # noqa: E402
from qts_rebound.stage2.fills import orders_table, sessions  # noqa: E402

OUT = REPO / "data" / "explore"
PANEL = OUT / "panel"
FEATS = ["relvol", "gap_sig", "depth_sig", "bars_before", "first_bar_rel", "trig_rel", "mfe", "mae"]


def build_month(month: str, o: pd.DataFrame, position: float = engine.POSITION, entries=None) -> pd.DataFrame:
    bars = pd.read_parquet(s2.MINUTES / f"{month}.parquet")
    bars["date"] = pd.to_datetime(bars["date"])
    sess = sessions(bars[bars["sym"] == "IWM"])
    o = o.merge(sess, on="date", how="left")
    o["close"] = o["close"].fillna(CLOSE)
    o["cutoff"] = o["cutoff"].fillna(CLOSE - 15)
    pre = premarket_dollar_volume(bars[bars["sym"].isin(o["sym"].unique())], PRE_OPEN, PRE_LAST)
    pre["date"] = pd.to_datetime(pre["date"])
    o = o.merge(pre, on=["date", "sym"], how="left")
    o["pre_dv"] = o["pre_dv"].fillna(0.0)
    o = o.sort_values(["date", "sym"]).reset_index(drop=True)
    o["oid"] = np.arange(len(o))
    b = bars[(bars["minute"] >= OPEN) & (bars["minute"] < CLOSE)].merge(
        o[["date", "sym", "oid"]], on=["date", "sym"], how="inner"
    )
    b = b.sort_values(["oid", "minute"], kind="stable")
    counts = np.bincount(b["oid"].to_numpy(), minlength=len(o))
    off = np.concatenate([[0], np.cumsum(counts)]).astype(np.int64)
    barr = {k: b[k].to_numpy(dtype=np.float64) for k in ("o", "h", "l", "c", "v")}
    barr["minute"] = b["minute"].to_numpy(dtype=np.int64)
    cprev_u = (o["c_prev"] * o["factor"]).to_numpy(dtype=np.float64)
    orders = {
        "cprev": cprev_u,
        "sigma": o["sigma"].to_numpy(dtype=np.float64),
        "cu": o["cu"].to_numpy(dtype=np.float64),
        "cutoff": o["cutoff"].to_numpy(dtype=np.int64),
        "close": o["close"].to_numpy(dtype=np.int64),
        "advsh": (o["adv_prev"].fillna(0.0) / cprev_u).to_numpy(dtype=np.float64),
    }
    r = engine.run(off, barr, orders, entries=entries or engine.ENTRIES, position=position)
    idx = r["oid"]
    t = o.iloc[idx][["date", "ticker", "sym", "news", "adv_prev", "sigma", "cu", "pre_dv"]].reset_index(
        drop=True
    )
    t["cprev_u"] = cprev_u[idx]
    t["e"] = r["e"].astype(np.int16)
    t["emin"] = r["minute"].astype(np.int16)
    t["px"] = r["px"]
    t["stress"] = r["stress"]
    t["shares"] = r["sh"]
    t["q_auction"] = r["qa"]
    t["fill_px"] = r["px"]
    for i, f in enumerate(FEATS):
        t[f] = r["feat"][:, i].astype(np.float32)
    for i in range(len(engine.EXITS)):
        t[f"r{i}"] = r["ret"][:, i].astype(np.float32)
    t["cost"] = costs(t).astype(np.float32) if len(t) else np.float32(0)
    return t.drop(columns=["fill_px"])


def cmd_build(a) -> None:
    panel = PANEL if a.position == engine.POSITION else OUT / f"panel_pos{a.position:g}"
    panel.mkdir(parents=True, exist_ok=True)
    cands = s2.micro_candidates(no_news_only=False)
    o_all = orders_table(cands, pd.read_parquet(s2.MAPPING), position_usd=a.position)
    o_all["month"] = o_all["date"].dt.strftime("%Y-%m")
    t0 = time.monotonic()
    for month in sorted(o_all["month"].unique()):
        f = panel / f"{month}.parquet"
        if (f.exists() and not a.force) or not (s2.MINUTES / f"{month}.parquet").exists():
            continue
        if a.budget and time.monotonic() - t0 > a.budget:
            print("budget reached; run again")
            return
        t = build_month(month, o_all[o_all["month"] == month].drop(columns=["month"]), a.position)
        t.to_parquet(f, index=False)
        print(f"  {month}: {len(t):,} rows ({time.monotonic() - t0:.0f}s)", flush=True)
    print("build: done")


# ------------------------------------------------------------------ scan

WINDOWS = (571, 575, 580, 600, 960)  # the order is cancelled if it has not filled by this minute
PRE_MAX = (None, 0.05, 0.01, 0.001)  # pre-market dollar volume (04:00-09:24) as a share of ADV
FAMILY_ENTRIES = (
    "lim2",
    "lim2_abs5",
    "lim2_abs8",
    "lim2_abs12",
    "lim2_0.02_abs5",
    "lim2_0.05_abs5",
    "rlim2_abs5",
    "vol2_0_abs5",
)
FAMILY_EXITS = ("moc", "t15", "t60", "tgt8")
HEADLINE = {  # the rules the note discusses; all others are sensitivity
    "open_sweep_5": ("lim2_abs5", 575, 0.01, "moc"),
    "open_sweep_5_t15": ("lim2_abs5", 575, 0.01, "t15"),
    "open_sweep_8": ("lim2_abs8", 575, 0.01, "moc"),
    "open_sweep_5_strict": ("lim2_0.05_abs5", 575, 0.01, "moc"),
    "open_sweep_5_reactive": ("vol2_0_abs5", 576, 0.01, "moc"),
}
PERIODS = (("2003-2016", 2003, 2016), ("2017-2023", 2017, 2023), ("2020-2023", 2020, 2023))


def _stats(x: pd.DataFrame, col: str, days: pd.DatetimeIndex) -> dict:
    from qts_rebound.stage1 import nw_t

    if len(x) == 0:
        return {"n": 0}
    g = x[col].to_numpy(dtype=float)
    net = g - x["cost"].to_numpy(dtype=float)
    s = pd.Series(net).groupby(x["date"].to_numpy()).sum().reindex(days, fill_value=0.0)
    return {
        "n": len(x),
        "per_day": round(len(x) / len(days), 3),
        "gross": float(g.mean()),
        "net": float(net.mean()),
        "net_median": float(np.median(net)),
        "wins": float((net > 0).mean()),
        "net_p5": float(np.quantile(net, 0.05)),
        "net_stress": float(net.mean() - stats.STRESS_RT),
        "nw_t": float(nw_t(s.to_numpy(), 5)[1]),
    }


def _periods(x: pd.DataFrame, col: str, days: pd.DatetimeIndex) -> dict:
    y, dy = x["date"].dt.year, days.year
    return {lab: _stats(x[(y >= a) & (y <= b)], col, days[(dy >= a) & (dy <= b)]) for lab, a, b in PERIODS}


def cmd_scan(a) -> None:
    import json

    t = stats.load(PANEL)
    t = t[~t["news"]].copy()
    t["pre_rel"] = t["pre_dv"] / t["adv_prev"]
    days = s2.trading_days()
    en, xn = stats.names()
    rows = []
    for e, ename in enumerate(en):  # 1: every entry x exit, unconditioned
        te = t[t["e"] == e]
        for x, xname in enumerate(xn):
            for lab, st in _periods(te, f"r{x}", days).items():
                rows.append(
                    {
                        "set": "grid",
                        "entry": ename,
                        "exit": xname,
                        "window": 960,
                        "pre_max": None,
                        "period": lab,
                        **st,
                    }
                )
    for ename in FAMILY_ENTRIES:  # 2: the opening-window family
        te = t[t["e"] == en.index(ename)]
        for w in WINDOWS:
            tw = te[te["emin"] <= w]
            for pm in PRE_MAX:
                tp = tw if pm is None else tw[tw["pre_rel"] < pm]
                for xname in FAMILY_EXITS:
                    for lab, st in _periods(tp, f"r{xn.index(xname)}", days).items():
                        rows.append(
                            {
                                "set": "open",
                                "entry": ename,
                                "exit": xname,
                                "window": w,
                                "pre_max": pm,
                                "period": lab,
                                **st,
                            }
                        )
    r = pd.DataFrame(rows)
    OUT.mkdir(parents=True, exist_ok=True)
    r.to_parquet(OUT / "scan.parquet", index=False)
    n_var = len(r) // len(PERIODS)
    head = {}
    for name, (ename, w, pm, xname) in HEADLINE.items():
        x = t[(t["e"] == en.index(ename)) & (t["emin"] <= w) & (t["pre_rel"] < pm)]
        col = f"r{xn.index(xname)}"
        net = x[col] - x["cost"]
        head[name] = {
            "trial": name,
            "entry": ename,
            "window": w,
            "pre_max": pm,
            "exit": xname,
            "periods": _periods(x, col, days),
            "by_year_net": {
                str(k): round(float(v), 5) for k, v in net.groupby(x["date"].dt.year).mean().items()
            },
            "by_year_n": {str(k): int(v) for k, v in net.groupby(x["date"].dt.year).size().items()},
        }
    (OUT / "results_open_sweep.json").write_text(
        json.dumps({"variants_looked_at": n_var, "rules": head}, indent=1)
    )
    print(f"{n_var} variants -> {OUT / 'scan.parquet'}")
    for name, h in head.items():
        print(
            name,
            {
                k: (v.get("n"), round(v.get("net", 0) * 100, 2), round(v.get("nw_t", 0), 1))
                for k, v in h["periods"].items()
            },
        )


TICK_SEED = 20261115
TICK_N = 200  # per period


def cmd_tick_sample(a) -> None:
    """A random sample of open_sweep_5 fills (half before 2017, half after) for scripts/massive.py ticks."""
    t = stats.load(PANEL)
    en, _ = stats.names()
    ename, w, pm, _x = HEADLINE["open_sweep_5"]
    x = t[
        (~t["news"]) & (t["e"] == en.index(ename)) & (t["emin"] <= w) & (t["pre_dv"] < pm * t["adv_prev"])
    ].copy()
    x["net"] = x["r0"] - x["cost"]
    parts = [
        g.sample(n=min(TICK_N, len(g)), random_state=TICK_SEED)
        for _, g in x.groupby(x["date"].dt.year >= 2017)
    ]
    s = pd.concat(parts).sort_values(["date", "sym"])
    s = s.rename(columns={"emin": "entry_minute", "px": "limit"})
    s["date"] = s["date"].dt.strftime("%Y-%m-%d")
    cols = ["date", "sym", "ticker", "entry_minute", "limit", "shares", "q_auction", "cprev_u", "cu", "net"]
    s[cols].to_csv(OUT / "tick_sample.csv", index=False)
    print(f"{len(s)} fills -> {OUT / 'tick_sample.csv'}")


def cmd_tick_check(a) -> None:
    import json

    from qts_rebound.explore import ticks
    from qts_rebound.stage2.data import MASSIVE_ROOT

    f = MASSIVE_ROOT / "quotes" / "tick_check.jsonl"
    if not f.exists():
        raise SystemExit(f"no ticks yet ({f}); on the Mac: python3 scripts/massive.py ticks")
    c = ticks.check(OUT / "tick_sample.csv", f)
    c.to_parquet(OUT / "tick_check.parquet", index=False)
    out = {"all": ticks.summary(c), "2017_on": ticks.summary(c[c["year"] >= 2017])}
    (OUT / "results_tick_check.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "scan", "tick-sample", "tick-check"])
    ap.add_argument("--budget", type=float, default=0)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--position", type=float, default=engine.POSITION)
    a = ap.parse_args()
    {"build": cmd_build, "scan": cmd_scan, "tick-sample": cmd_tick_sample, "tick-check": cmd_tick_check}[
        a.cmd
    ](a)


if __name__ == "__main__":
    main()
