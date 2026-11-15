"""Stage 2 on hand-made data: name matching, minute extraction, the fill model, statistics."""

from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from qts_rebound.stage2 import data as sdata
from qts_rebound.stage2.evaluate import capped, daily_pnl, evaluate, folds_positive, holm, verdicts
from qts_rebound.stage2.extract import extract_files
from qts_rebound.stage2.fills import FillParams, orders_table, sessions, simulate, trades
from qts_rebound.stage2.mapping import alias_table, coverage, match

HEADER = "ticker,volume,open,close,high,low,window_start,transactions"


def _gz(path: Path, rows: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(("\n".join([HEADER, *rows]) + "\n").encode()))
    return path


def _ns(d: str, hh: int, mm: int, utc_offset_h: int) -> int:
    t = dt.datetime.fromisoformat(d).replace(
        hour=hh, minute=mm, tzinfo=dt.timezone(dt.timedelta(hours=utc_offset_h))
    )
    return int(t.timestamp()) * 1_000_000_000


def test_alias_table_forms():
    sm = pd.DataFrame(
        {"ticker": ["AAAB1", "BRK.B", "META", "X"], "relatedtickers": ["AAAB", "BRK.A", "FB METAM", None]}
    )
    a = alias_table(sm)
    got = {t: set(g["sym"]) for t, g in a.groupby("ticker")}
    assert got["AAAB1"] == {"AAAB"}
    assert got["BRK.B"] == {"BRK.B", "BRK-B", "BRK/B", "BRKB", "BRK.A", "BRK-A", "BRK/A", "BRKA"}
    assert got["META"] == {"META", "FB", "METAM"}
    own = a.set_index(["ticker", "sym"])["own"]
    assert own[("BRK.B", "BRKB")] and not own[("BRK.B", "BRK.A")] and not own[("META", "FB")]


def test_match_rules(tmp_path):
    d1 = "2010-03-01"
    f = _gz(
        tmp_path / f"{d1}.csv.gz",
        [
            "ABC,100000,1,10.00,1,1,0,1",  # reused ticker ABC1 -> ABC
            "FB,500000,1,20.05,1,1,0,1",  # META's old symbol
            "BRK.B,3000,1,70.00,1,1,0,1",
            "GOOGL,1000,1,500.00,1,1,0,1",
            "GOOG,1000,1,501.00,1,1,0,1",  # 0.2 % away: GOOGL's own symbol must win
            "ZZZ,800,1,33.333,1,1,0,1",  # unique price match for a ticker with no alias
            "P1X,1000,1,44.44,1,1,0,1",
            "P2X,1000,1,44.44,1,1,0,1",  # two symbols at the same price: ambiguous
            "VOL,1000,1,5.00,1,1,0,1",  # volume 10x off: no match
        ],
    )
    sm = pd.DataFrame(
        {
            "ticker": ["ABC1", "META", "BRK.B", "GOOGL", "NEWNAME", "AMB", "VOL"],
            "relatedtickers": ["ABC", "FB", "BRK.A", "GOOG", None, None, None],
        }
    )
    cands = pd.DataFrame(
        {
            "date": pd.to_datetime([d1] * 7),
            "ticker": ["ABC1", "META", "BRK.B", "GOOGL", "NEWNAME", "AMB", "VOL"],
            # Sharadar: split-adjusted close c and volume v, as-traded close cu
            "cu": [10.01, 20.0, 70.0, 500.0, 33.34, 44.44, 5.0],
            "c": [10.01, 10.0, 70.0, 500.0, 33.34, 44.44, 5.0],  # META later split 2:1
            "v": [100000.0, 1_000_000.0, 3000.0, 1000.0, 800.0, 1000.0, 100.0],
        }
    )
    m = match(cands, alias_table(sm), [f]).set_index("ticker")
    assert m.loc["ABC1", "sym"] == "ABC" and m.loc["ABC1", "method"] == "alias"
    assert m.loc["META", "sym"] == "FB"  # 20.05 vs 20.00 within 0.5 %, volume 1M x 10/20 = 500k
    assert m.loc["BRK.B", "sym"] == "BRK.B"
    assert m.loc["GOOGL", "sym"] == "GOOGL"
    assert m.loc["NEWNAME", "sym"] == "ZZZ" and m.loc["NEWNAME", "method"] == "price"
    assert "AMB" not in m.index and "VOL" not in m.index
    cov = coverage(cands, m.reset_index())
    assert cov.loc[0, "stock_days"] == 7 and cov.loc[0, "matched"] == 5


def test_research_files_and_seal(tmp_path, monkeypatch):
    base = tmp_path / "raw" / "us_stocks_sip" / "minute_aggs_v1"
    _gz(base / "2023/11/2023-11-08.csv.gz", [])
    _gz(tmp_path / "sealed/us_stocks_sip/minute_aggs_v1/2023/11/2023-11-09.csv.gz", [])
    got = sdata.research_files("minute_aggs", root=tmp_path)
    assert [d for d, _ in got] == [dt.date(2023, 11, 8)]  # the sealed folder is never listed
    _gz(base / "2023/11/2023-11-10.csv.gz", [])  # a holdout file in the wrong place
    with pytest.raises(sdata.SealViolation):
        sdata.research_files("minute_aggs", root=tmp_path)


def test_utc_offsets():
    assert sdata.utc_offset_minutes(dt.date(2020, 7, 1)) == -240
    assert sdata.utc_offset_minutes(dt.date(2020, 1, 2)) == -300
    assert sdata.utc_offset_minutes(dt.date(2007, 3, 12)) == -240  # 2007 rules: DST from March 11


def test_extract_minutes_new_york_time(tmp_path):
    summer, winter = "2020-07-01", "2020-01-02"
    rows_s = [
        f"AAA,100,1,1,1,1,{_ns(summer, 13, 30, 0)},1",  # 09:30 EDT
        f"AAA,100,1,1,1,1,{_ns(summer, 7, 59, 0)},1",  # 03:59 EDT: dropped
        f"AAA,100,1,1,1,1,{_ns(summer, 20, 0, 0)},1",  # 16:00 EDT: dropped
        f"IWM,100,1,1,1,1,{_ns(summer, 19, 59, 0)},1",  # 15:59 EDT, always kept
        f"BBB,100,1,1,1,1,{_ns(summer, 14, 0, 0)},1",  # not wanted
    ]
    rows_w = [f"AAA,7,1,1,1,1,{_ns(winter, 14, 30, 0)},3", f"SPY,1,1,1,1,1,{_ns(winter, 9, 0, 0)},1"]
    f1 = _gz(tmp_path / f"{summer}.csv.gz", rows_s)
    f2 = _gz(tmp_path / f"{winter}.csv.gz", rows_w)
    wanted = pd.DataFrame({"date": pd.to_datetime([summer, winter]), "sym": ["AAA", "AAA"]})
    out = tmp_path / "m.parquet"
    n = extract_files([(dt.date(2020, 7, 1), f1), (dt.date(2020, 1, 2), f2)], wanted, out, threads=1)
    x = pd.read_parquet(out)
    assert n == len(x) == 4
    got = {(str(r.date)[:10], r.sym, int(r.minute)) for r in x.itertuples()}
    assert got == {(summer, "AAA", 570), (summer, "IWM", 959), (winter, "AAA", 570), (winter, "SPY", 240)}


def _bars(rows):
    return pd.DataFrame(rows, columns=["date", "sym", "minute", "o", "h", "l", "c", "v"]).assign(
        date=lambda d: pd.to_datetime(d["date"])
    )


def _orders(rows):
    o = pd.DataFrame(rows, columns=["date", "ticker", "sym", "limit", "q", "cu", "adv_prev", "sigma"])
    o["date"] = pd.to_datetime(o["date"])
    return o


D, E = "2020-07-01", "2020-11-27"  # E closes early (13:00)


def test_fill_model_cases():
    orders = _orders(
        [
            (D, "A", "A", 10.0, 50, 10.4, 1e6, 0.03),  # continuous: touch, then trade-through with volume
            (D, "B", "B", 10.0, 50, 10.0, 1e6, 0.03),  # through early with thin volume, volume later
            (D, "C", "C", 10.0, 50, 10.2, 1e6, 0.03),  # opening auction + continuous remainder
            (D, "Dd", "Dd", 10.0, 50, 10.0, 1e6, 0.03),  # first bar 09:31 below the limit: no auction
            (D, "F", "F", 10.0, 50, 10.0, 1e6, 0.03),  # only after the cut-off
            (D, "G", "G", 10.0, 50, 10.0, 1e6, 0.03),  # auction with a tiny first minute
            (E, "H", "H", 10.0, 50, 10.0, 1e6, 0.03),  # early-close day: 12:50 is after its cut-off
        ]
    )
    bars = _bars(
        [
            (D, "A", 570, 10.5, 10.6, 10.30, 10.4, 500),
            (D, "A", 571, 10.3, 10.3, 9.98, 10.0, 60),  # touches L (9.98 <= 10), not through (9.95)
            (D, "A", 572, 10.0, 10.0, 9.94, 9.96, 50),  # through; touch volume 110 >= 2 x 50
            (D, "B", 571, 10.0, 10.0, 9.90, 9.95, 30),  # through, touch volume 30
            (D, "B", 575, 10.1, 10.1, 9.99, 10.0, 80),  # touch only; touch volume now 110
            (D, "C", 570, 9.50, 9.60, 9.40, 9.55, 100),  # opens below L: auction part min(50, 25) = 25
            (D, "Dd", 571, 9.50, 9.60, 9.40, 9.55, 1000),
            (D, "F", 945, 9.50, 9.60, 9.40, 9.55, 1000),  # 15:45 bar: cut off
            (D, "G", 570, 9.80, 9.90, 9.70, 9.85, 3),  # floor(0.75) = 0 shares at the open
            (D, "G", 571, 9.80, 9.90, 9.70, 9.85, 200),
            (E, "H", 770, 9.50, 9.60, 9.40, 9.55, 1000),  # 12:50 > 12:45 cut-off
            (D, "IWM", 959, 1, 1, 1, 1, 1),
            (E, "IWM", 779, 1, 1, 1, 1, 1),
        ]
    )
    sess = sessions(bars[bars["sym"] == "IWM"])
    assert sess.set_index("date").loc[pd.Timestamp(E), "cutoff"] == 765
    assert sess.set_index("date").loc[pd.Timestamp(D), "cutoff"] == 945
    f = simulate(orders, bars, sess).set_index("ticker")
    a = f.loc["A"]
    assert a["filled"] and a["kind"] == "continuous" and a["fill_px"] == 10.0 and a["entry_minute"] == 573
    b = f.loc["B"]
    assert b["filled"] and b["cont_minute"] == 575 and b["entry_minute"] == 576
    c = f.loc["C"]
    assert c["kind"] == "auction+continuous" and c["q_auction"] == 25 and c["q_cont"] == 25
    assert math.isclose(c["fill_px"], (25 * 9.5 + 25 * 10.0) / 50) and c["entry_minute"] == 570
    dd = f.loc["Dd"]
    assert dd["kind"] == "continuous" and dd["q_auction"] == 0 and dd["fill_px"] == 10.0
    assert not f.loc["F", "filled"] and not f.loc["H", "filled"]
    g = f.loc["G"]
    assert g["q_auction"] == 0 and g["kind"] == "continuous" and g["entry_minute"] == 572
    lim = simulate(orders, bars, sess, FillParams(auction_at_limit=True)).set_index("ticker")
    assert lim.loc["C", "fill_px"] == 10.0
    novol = simulate(orders, bars, sess, FillParams(vol_mult=0.0)).set_index("ticker")
    assert novol.loc["B", "entry_minute"] == 572  # without the volume test B fills on the first through
    t = trades(f.reset_index())
    assert math.isclose(t.set_index("ticker").loc["A", "r"], 10.4 / 10.0 - 1)


def test_orders_table_as_traded_limit_and_shares():
    cands = pd.DataFrame(
        {
            "date": pd.to_datetime(["2020-07-01"]),
            "ticker": ["A"],
            "c_prev": [5.0],
            "sigma": [0.05],
            "c": [4.0],
            "cu": [8.0],  # a later 1:2 reverse split: as-traded prices are twice the adjusted ones
        }
    )
    mp = pd.DataFrame({"date": pd.to_datetime(["2020-07-01"]), "ticker": ["A"], "sym": ["A"]})
    o = orders_table(cands, mp).iloc[0]
    assert math.isclose(o["limit"], 5.0 * 0.9 * 2.0) and o["q"] == math.floor(500 / 9.0)


def test_statistics_and_verdicts():
    days = pd.bdate_range("2020-01-01", periods=60)
    rng = np.random.default_rng(1)
    rows = []
    for d in days:
        for i in range(25):
            rows.append((d, f"T{i:02d}", 570 + i, 1e6 - i, rng.normal(0.01, 0.01)))
    t = pd.DataFrame(rows, columns=["date", "ticker", "entry_minute", "adv_prev", "r"])
    t["cost"] = 0.002
    t["net"] = t["r"] - t["cost"]
    t["kind"] = "continuous"
    s = daily_pnl(t, pd.DatetimeIndex([*days, pd.Timestamp("2020-12-31")]))
    assert s.iloc[-1] == 0.0 and math.isclose(s.iloc[0], t[t["date"] == days[0]]["net"].sum())
    cp = capped(t)
    assert cp.groupby("date").size().max() == 20 and set(cp["ticker"]) == {f"T{i:02d}" for i in range(20)}
    assert folds_positive(pd.Series([1.0, -2, 3, 4, -5, 6, 1, 1, -1, -1, 1, 1])) == 4
    assert holm([0.01, 0.04, 0.03]) == pytest.approx([0.03, 0.06, 0.06])
    r = verdicts([evaluate("X", t, days)])[0]
    assert r.trades == 1500 and r.capped_trades == 1200 and r.pass_all


def test_prereg_hash_is_the_registered_one():
    import importlib.util

    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("run_stage2", root / "scripts" / "run_stage2.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    h = hashlib.sha256((root / "docs/notes/2026-11-14_preregistration_stage2.md").read_bytes()).hexdigest()[
        :12
    ]
    assert h == mod.REGISTERED


def test_costs_small_order_sane():
    pytest.importorskip("qts")
    from qts_rebound.stage2.evaluate import costs

    t = pd.DataFrame(
        {
            "date": pd.to_datetime(["2020-07-01", "2020-07-01"]),
            "shares": [100.0, 100.0],
            "fill_px": [5.0, 5.0],
            "q_auction": [0.0, 100.0],
            "cu": [5.1, 5.1],
            "adv_prev": [1e6, 1e6],
            "sigma": [0.04, 0.04],
        }
    )
    c = costs(t)
    assert (c > 0).all() and (c < 0.02).all()
    assert c.iloc[1] - c.iloc[0] == pytest.approx(100 * 0.0015 / 500)  # the auction fee on the auction part
