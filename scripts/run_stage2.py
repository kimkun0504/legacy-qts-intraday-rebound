"""Stage 2 (docs/notes/2026-11-14_preregistration_stage2.md), step by step:

    python scripts/run_stage2.py map            # Sharadar -> Massive symbols from the daily bars
    python scripts/run_stage2.py extract        # minute bars of the candidate stock-days (resumable)
    python scripts/run_stage2.py gates          # data-quality gates; no return is computed
    python scripts/run_stage2.py fills          # the fill model, month by month (resumable)
    python scripts/run_stage2.py eval           # P1-P3, Holm, pass rules, ledger
    python scripts/run_stage2.py quote-sample   # the 500 fills for the quote check

``--budget SECONDS`` stops extract/fills cleanly between months (the VM's calls are short).
``fills`` and ``eval`` refuse to run before the gates pass, with uncommitted code, or if the
pre-registration file changed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
import warnings
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from qts_rebound.paths import LEDGER, OUT, PANEL_DB, SHARED_STORE  # noqa: E402
from qts_rebound.stage2.data import (  # noqa: E402
    CLOSE,
    OPEN,
    PRE_LAST,
    PRE_OPEN,
    STAGE2,
    WINDOW_START,
    last_research_day,
    research_files,
)
from qts_rebound.stage2.evaluate import (  # noqa: E402
    as_dict,
    costs,
    evaluate,
    premarket_dollar_volume,
    verdicts,
)
from qts_rebound.stage2.extract import extract_files, months  # noqa: E402
from qts_rebound.stage2.fills import FillParams, orders_table, sessions, simulate, trades  # noqa: E402
from qts_rebound.stage2.mapping import alias_table, coverage, match  # noqa: E402

PREREG = REPO / "docs/notes/2026-11-14_preregistration_stage2.md"
REGISTERED = "ed85b682c42e"
FAMILY = "intraday_rebound_stage2:" + REGISTERED
MAPPING = STAGE2 / "mapping.parquet"
MINUTES = STAGE2 / "minute"
GATES = STAGE2 / "gates.json"
FILLS = STAGE2 / "fills"
PRE_SHARE_MAX = 0.05
ADV_P2 = 1e6
QUOTE_SEED = 20261114
QUOTE_N = 500
CAND_COLS = ["date", "ticker", "c_prev", "c", "cu", "o", "lo", "v", "sigma", "adv_prev", "news"]


def micro_candidates(no_news_only: bool) -> pd.DataFrame:
    c = pd.read_parquet(OUT / "candidates_micro.parquet", columns=CAND_COLS)
    c["date"] = pd.to_datetime(c["date"])
    c = c[(c["date"] >= pd.Timestamp(WINDOW_START)) & (c["date"] <= pd.Timestamp(last_research_day()))]
    return c[~c["news"]].copy() if no_news_only else c


def trading_days() -> pd.DatetimeIndex:
    con = duckdb.connect(str(PANEL_DB), read_only=True)
    d = con.execute(
        "select date from cal where date between ? and ? order by date",
        [pd.Timestamp(WINDOW_START), pd.Timestamp(last_research_day())],
    ).df()["date"]
    con.close()
    return pd.DatetimeIndex(pd.to_datetime(d))


def prereg_ok() -> None:
    h = hashlib.sha256(PREREG.read_bytes()).hexdigest()[:12]
    if h != REGISTERED:
        raise SystemExit(
            f"the pre-registration changed (sha {h} != {REGISTERED}); stage 2 is bound to the original"
        )


def committed_code() -> str:
    dirty = subprocess.run(
        ["git", "-C", str(REPO), "status", "--porcelain", "--", "qts_rebound", "scripts", str(PREREG)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if dirty:
        raise SystemExit("uncommitted changes in the code; commit before running:\n" + dirty)
    return subprocess.run(
        ["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()


# ------------------------------------------------------------------ map


def cmd_map(a) -> None:
    cands = micro_candidates(no_news_only=False)
    sm = pd.read_parquet(SHARED_STORE / "security_master", columns=["ticker", "relatedtickers"])
    alias = alias_table(sm)
    files = research_files("day_aggs")
    have = {d for d, _ in files}
    missing_days = sorted({d.date() for d in cands["date"]} - have)
    out = []
    for y, cy in cands.groupby(cands["date"].dt.year):
        fy = [p for d, p in files if d.year == y]
        if not fy:
            continue
        out.append(match(cy, alias, fy))
        print(f"  {y}: {len(cy):,} candidate stock-days, matched {len(out[-1]):,}", flush=True)
    mp = pd.concat(out, ignore_index=True)
    STAGE2.mkdir(parents=True, exist_ok=True)
    mp.to_parquet(MAPPING, index=False)
    rep = {
        "day_files": len(files),
        "candidate_days_without_file": [str(d) for d in missing_days[:50]],
        "n_candidate_days_without_file": len(missing_days),
        "methods": mp["method"].value_counts().to_dict(),
    }
    for name, sub in gate_sets(cands).items():
        cov = coverage(sub, mp)
        rep[name] = cov.reset_index().rename(columns={"date": "year"}).to_dict(orient="records")
        print(
            f"{name}: matched {cov.loc[0, 'share']:.4f} overall, worst year {cov.drop(index=0)['share'].min():.4f}"
        )
    (STAGE2 / "mapping_report.json").write_text(json.dumps(rep, indent=1, default=str))
    print(f"mapping: {len(mp):,} matched stock-days -> {MAPPING}")


def gate_sets(cands: pd.DataFrame) -> dict[str, pd.DataFrame]:
    p1 = cands[~cands["news"]]
    return {"P1_set": p1, "P2_set": p1[p1["adv_prev"] >= ADV_P2]}


# ------------------------------------------------------------------ extract


def cmd_extract(a) -> None:
    mp = pd.read_parquet(MAPPING)
    cands = micro_candidates(no_news_only=False)[["date", "ticker"]]
    wanted = cands.merge(mp[["date", "ticker", "sym"]], on=["date", "ticker"])[["date", "sym"]]
    wanted["month"] = wanted["date"].dt.strftime("%Y-%m")
    t0 = time.monotonic()
    done = skipped = 0
    for month, fl in months(research_files("minute_aggs")).items():
        out = MINUTES / f"{month}.parquet"
        if out.exists():
            skipped += 1
            continue
        if a.budget and time.monotonic() - t0 > a.budget:
            print(f"budget reached; {done} months written this run; run again to continue")
            return
        n = extract_files(fl, wanted[wanted["month"] == month], out)
        done += 1
        print(f"  {month}: {len(fl)} files -> {n:,} bars ({time.monotonic() - t0:.0f}s)", flush=True)
    print(f"extract: {done} months written, {skipped} already there")


# ------------------------------------------------------------------ gates


def cmd_gates(a) -> None:
    rep = json.loads((STAGE2 / "mapping_report.json").read_text())
    res: dict = {"family": FAMILY}
    ok = True
    for name in ("P1_set", "P2_set"):
        rows = {r["year"]: r for r in rep[name]}
        overall = rows[0]["share"]
        worst = min(r["share"] for y, r in rows.items() if y != 0)
        g = overall >= 0.95 and worst >= 0.90
        res[name] = {"matched_overall": overall, "matched_worst_year": worst, "pass": g}
        ok &= g
    month_files = {p.stem for p in MINUTES.glob("*.parquet")}
    need = set(months(research_files("minute_aggs")))
    if need - month_files:
        raise SystemExit(f"extract is not finished: {len(need - month_files)} months missing")
    mp = pd.read_parquet(MAPPING)
    p1 = micro_candidates(no_news_only=True)[["date", "ticker"]].merge(
        mp[["date", "ticker", "sym"]], on=["date", "ticker"]
    )
    con = duckdb.connect()
    con.register("p1", p1)
    hit = con.execute(
        f"""select count(*) from (select distinct date, sym from p1) x
        semi join (select distinct date, sym from read_parquet('{MINUTES}/*.parquet')
                   where minute between {OPEN} and {CLOSE - 1}) b
        on b.date = x.date and b.sym = x.sym"""
    ).fetchone()[0]
    n = int(p1[["date", "sym"]].drop_duplicates().shape[0])
    share = hit / max(n, 1)
    res["minute_coverage"] = {
        "matched_p1_stock_days": n,
        "with_regular_bars": int(hit),
        "share": share,
        "pass": share >= 0.95,
    }
    ok &= share >= 0.95
    res["pass"] = bool(ok)
    GATES.write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1))


def gates_passed() -> None:
    if not GATES.exists() or not json.loads(GATES.read_text()).get("pass"):
        raise SystemExit("the data-quality gates have not passed (run gates); nothing is evaluated")


# ------------------------------------------------------------------ fills


VARIANTS = {
    "base": FillParams(),
    "through0": FillParams(through=0.0),
    "through1": FillParams(through=0.01),
    "through2": FillParams(through=0.02),
    "alpha10": FillParams(alpha=0.10),
    "alpha50": FillParams(alpha=0.50),
    "novol": FillParams(vol_mult=0.0),
    "auction_at_limit": FillParams(auction_at_limit=True),
    "cutoff1530": FillParams(cutoff=15 * 60 + 30),
}


def cmd_fills(a) -> None:
    prereg_ok()
    gates_passed()
    code = committed_code()
    variants = [v.strip() for v in a.variants.split(",")]
    mp = pd.read_parquet(MAPPING)
    cands = micro_candidates(no_news_only=True)
    orders_all = orders_table(cands, mp)
    orders_all["month"] = orders_all["date"].dt.strftime("%Y-%m")
    t0 = time.monotonic()
    for month in sorted(orders_all["month"].unique()):
        todo = [v for v in variants if not (FILLS / v / f"{month}.parquet").exists()]
        if not todo:
            continue
        if a.budget and time.monotonic() - t0 > a.budget:
            print("budget reached; run again to continue")
            return
        bars = pd.read_parquet(MINUTES / f"{month}.parquet")
        sess = sessions(bars[bars["sym"] == "IWM"])
        o = orders_all[orders_all["month"] == month]
        pre = premarket_dollar_volume(bars[bars["sym"].isin(o["sym"].unique())], PRE_OPEN, PRE_LAST)
        o = o.merge(pre, on=["date", "sym"], how="left")
        o["pre_dv"] = o["pre_dv"].fillna(0.0)
        for v in todo:
            f = simulate(o, bars, sess, VARIANTS[v])
            f["code"] = code
            (FILLS / v).mkdir(parents=True, exist_ok=True)
            f.drop(columns=["month"]).to_parquet(FILLS / v / f"{month}.parquet", index=False)
        print(f"  {month}: {len(o):,} orders, variants {todo} ({time.monotonic() - t0:.0f}s)", flush=True)
    print("fills: done")


def load_trades(variant: str = "base") -> pd.DataFrame:
    need = set(pd.date_range(WINDOW_START, last_research_day(), freq="MS").strftime("%Y-%m"))
    have = {p.stem for p in (FILLS / variant).glob("*.parquet")}
    if not need <= have:
        raise SystemExit(f"fills for {variant} not finished: {len(need - have)} months missing")
    f = pd.concat(
        [pd.read_parquet(p) for p in sorted((FILLS / variant).glob("*.parquet"))], ignore_index=True
    )
    t = trades(f)
    t["cost"] = costs(t)
    t["net"] = t["r"] - t["cost"]
    return t


def trial_sets(t: pd.DataFrame) -> dict[str, pd.DataFrame]:
    return {
        "P1": t,
        "P2": t[t["adv_prev"] >= ADV_P2],
        "P3": t[t["pre_dv"] < PRE_SHARE_MAX * t["adv_prev"]],
    }


def cmd_eval(a) -> None:
    warnings.simplefilter("ignore")
    prereg_ok()
    gates_passed()
    code = committed_code()
    t = load_trades("base")
    days = trading_days()
    res = verdicts([evaluate(k, v, days) for k, v in trial_sets(t).items()])
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a") as f:
        for r in res:
            rec = {
                "ts": pd.Timestamp.now(tz="UTC").isoformat(),
                "kind": "stage2_trial",
                "family": FAMILY,
                "code": code,
                **as_dict(r),
            }
            f.write(json.dumps(rec, default=float) + "\n")
    (STAGE2 / "results_stage2.json").write_text(
        json.dumps([as_dict(r) for r in res], indent=1, default=float)
    )
    for r in res:
        print(
            f"{r.trial}: {r.trades:,} trades ({r.fills_per_day:.1f}/day on {r.days_with_fills:,} days) | gross "
            f"{100 * r.gross_mean:+.3f}% (median {100 * r.gross_median:+.3f}%, win {100 * r.win_rate:.0f}%) | net "
            f"{100 * r.net_mean:+.3f}% [{r.pass_net}] | NW t {r.nw_t:.2f}, Holm p {r.holm_p:.2g} [{r.pass_t}] | "
            f"folds+ {r.folds_positive}/6 [{r.pass_folds}] | capped net {100 * r.capped_net_mean:+.3f}% "
            f"[{r.pass_capped}] | PASS {r.pass_all}"
        )


def cmd_quote_sample(a) -> None:
    t = load_trades("base")
    s = t.sample(n=min(QUOTE_N, len(t)), random_state=QUOTE_SEED).sort_values(["date", "sym"])
    s = s[["date", "sym", "ticker", "entry_minute", "kind", "limit", "fill_px", "close"]].copy()
    s["date"] = s["date"].dt.strftime("%Y-%m-%d")
    s.to_csv(STAGE2 / "quote_sample.csv", index=False)
    print(f"{len(s)} fills -> {STAGE2 / 'quote_sample.csv'}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("cmd", choices=["map", "extract", "gates", "fills", "eval", "quote-sample"])
    p.add_argument("--budget", type=float, default=None)
    p.add_argument("--variants", default="base")
    a = p.parse_args()
    {
        "map": cmd_map,
        "extract": cmd_extract,
        "gates": cmd_gates,
        "fills": cmd_fills,
        "eval": cmd_eval,
        "quote-sample": cmd_quote_sample,
    }[a.cmd](a)


if __name__ == "__main__":
    np.seterr(all="ignore")
    main()
