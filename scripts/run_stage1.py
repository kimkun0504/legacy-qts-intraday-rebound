"""Stage 1: build the candidate tables (``build``), then run the pre-registered trials and the
diagnostics (``eval``).  python scripts/run_stage1.py build|eval"""

from __future__ import annotations

import hashlib
import json
import sys
import warnings

import pandas as pd
from qts.research.seal import load_seal

from qts_rebound.paths import LEDGER, OUT, PANEL_DB, REPO_ROOT, SEAL_FILE, SHARED_STORE, SMALLCAP_STORE
from qts_rebound.stage1 import as_dict, candidates, costs, evaluate, news_flags, trades

PREREG = REPO_ROOT / "docs/notes/2026-11-14_preregistration_stage1.md"
FAMILY = "intraday_rebound_stage1:" + hashlib.sha256(PREREG.read_bytes()).hexdigest()[:12]
UNIS = {"small": "uni", "micro": "uni_micro"}


def build() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    end = load_seal(SEAL_FILE).load_end_iso
    for name, table in UNIS.items():
        c = candidates(str(PANEL_DB), str(SMALLCAP_STORE), str(SHARED_STORE), end, table)
        c["news"] = news_flags(c, str(SHARED_STORE))
        c.to_parquet(OUT / f"candidates_{name}.parquet", index=False)
        print(
            f"{name}: {len(c):,} candidate name-days, {c['date'].nunique():,} days, news share {c['news'].mean():.3f}"
        )


def holm(ps: list[float]) -> list[float]:
    order = sorted(range(len(ps)), key=lambda i: ps[i])
    adj, running = [0.0] * len(ps), 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (len(ps) - rank) * ps[i]))
        adj[i] = running
    return adj


def line(r) -> str:
    return (
        f"{r.trial}: {r.trades:,} trades on {r.days_with_fills:,} days ({r.fills_per_day:.1f}/day) | gross/day "
        f"{100 * r.gross_daily_mean:+.3f}% NW t={r.nw_t:.2f} [{r.pass_t}] | net/trade {100 * r.net_per_trade:+.3f}% "
        f"[{r.pass_net}] | folds+ {r.folds_positive}/6 [{r.pass_folds}] | raw gain/trade mean {100 * r.raw_gain_mean:+.2f}% "
        f"median {100 * r.raw_gain_median:+.2f}% win {100 * r.win_rate:.0f}% | cost {100 * r.cost_median:.2f}% | "
        f"gap fills {100 * r.gap_fill_share:.0f}% ({100 * r.gap_fill_mean:+.2f}%) intraday fills {100 * r.intraday_fill_mean:+.2f}% "
        f"| minus IWM {100 * (r.minus_iwm_mean or float('nan')):+.2f}%"
    )


def run_eval() -> None:
    warnings.simplefilter("ignore")
    sfp = pd.read_parquet(SMALLCAP_STORE / "sharadar_sfp", filters=[("ticker", "=", "IWM")])
    iwm_oc = pd.Series((sfp["close"] / sfp["open"] - 1).to_numpy(), index=pd.to_datetime(sfp["date"]))
    c = {n: pd.read_parquet(OUT / f"candidates_{n}.parquet") for n in UNIS}
    specs = {"R1": ("small", False), "R2": ("micro", False), "R3": ("small", True)}
    res = {}
    for trial, (u, news) in specs.items():
        t = trades(c[u][c[u]["news"] == news], k=2.0, through=0.005)
        res[trial] = evaluate(trial, t, costs(t), iwm_oc)
    adj = holm([res[k].p_two_sided for k in specs])
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a") as f:
        for r, a in zip(res.values(), adj, strict=True):
            ok = r.pass_t and a < 0.05 and r.pass_net and r.pass_folds
            rec = {
                "ts": pd.Timestamp.now(tz="UTC").isoformat(),
                "kind": "stage1_trial",
                "family": FAMILY,
                **as_dict(r),
                "holm_p": a,
                "pass_all": ok,
            }
            f.write(json.dumps(rec, default=float) + "\n")
            print(line(r), f"| Holm p={a:.4f} | PASS={ok}")
            print("   by year:", r.by_year)
    (OUT / "results_stage1.json").write_text(
        json.dumps({k: as_dict(v) for k, v in res.items()}, indent=1, default=float)
    )


def run_diag(which: str) -> None:
    warnings.simplefilter("ignore")
    sfp = pd.read_parquet(SMALLCAP_STORE / "sharadar_sfp", filters=[("ticker", "=", "IWM")])
    iwm_oc = pd.Series((sfp["close"] / sfp["open"] - 1).to_numpy(), index=pd.to_datetime(sfp["date"]))
    uni_name, news_tag = which.split("/")
    c = {uni_name: pd.read_parquet(OUT / f"candidates_{uni_name}.parquet")}
    print("Diagnostics (reported only, not selectable):")
    for u, news in ((uni_name, news_tag == "news"),):
        base = c[u][c[u]["news"] == news]
        for lab, kw in (
            ("k=1.5", {"k": 1.5}),
            ("k=3", {"k": 3.0}),
            ("through=0", {"through": 0.0}),
            ("through=2%", {"through": 0.02}),
            ("exit next close", {"exit_next": True}),
        ):
            args = {"k": 2.0, "through": 0.005, **kw}
            t = trades(base, **args)
            r = evaluate(f"{u}/{'news' if news else 'no-news'}/{lab}", t, costs(t), iwm_oc)
            print(" ", line(r))


if __name__ == "__main__":
    if sys.argv[1] == "diag":
        run_diag(sys.argv[2])
    else:
        {"build": build, "eval": run_eval}[sys.argv[1]]()
