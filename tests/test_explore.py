"""Exploratory engine: one synthetic stock-day checked by hand, and the tick check."""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("numba")

from qts_rebound.explore import engine, ticks


def _run(bars, entries, exits, cprev=10.0, sigma=0.03, cu=10.0):
    m, o, h, l, c, v = (np.array(x, dtype=np.float64) for x in zip(*bars, strict=True))
    barr = {"minute": m.astype(np.int64), "o": o, "h": h, "l": l, "c": c, "v": v}
    orders = {
        "cprev": np.array([cprev]),
        "sigma": np.array([sigma]),
        "cu": np.array([cu]),
        "cutoff": np.array([945], dtype=np.int64),
        "close": np.array([960], dtype=np.int64),
        "advsh": np.array([39_000.0]),
    }
    return engine.run(np.array([0, len(m)], dtype=np.int64), barr, orders, entries=entries, exits=exits)


BARS = [  # minute, o, h, l, c, v: a sweep through 9.40 in the first minute, then a rebound
    (570, 9.90, 9.90, 9.30, 9.60, 1000),
    (571, 9.60, 9.95, 9.50, 9.90, 500),
    (590, 9.90, 10.00, 9.85, 9.95, 400),
]


def test_resting_limit_fills_on_the_sweep_and_exits():
    r = _run(
        BARS, [("lim", 2, 0, 0.05)], [("moc", 0, 0), ("tgt", 0.05, 0), ("time", 15, 0), ("stop", 0, 0.03)]
    )
    assert len(r["px"]) == 1
    assert r["px"][0] == pytest.approx(9.4)  # 10 x (1 - max(2 x 3 %, 5 %))
    assert r["minute"][0] == 571 and r["sh"][0] == 53
    moc, tgt, t15, stop = r["ret"][0]
    assert moc == pytest.approx(10 / 9.4 - 1)
    assert tgt == pytest.approx(0.05)  # 9.95 >= 9.87 x 1.005
    assert t15 == pytest.approx(9.90 / 9.4 - 1)  # the first bar at least 15 minutes after the entry
    assert stop == pytest.approx(moc)  # never 3 % below the fill


def test_reactive_orders_miss_the_sweep_and_strict_fill_needs_more():
    r = _run(BARS, [("rlim", 2, 0, 0.05), ("vol", 2, 0, 0.05), ("lim", 2, 0.05, 0.05)], [("moc", 0, 0)])
    # rlim: the limit never trades again; strict lim: 9.30 is not 5 % through 9.40; vol: buys 9.60
    assert list(r["e"]) == [1]
    assert r["px"][0] == pytest.approx(9.60) and r["stress"][0] == pytest.approx(9.95)


def test_floor_on_the_drop_and_names():
    r = _run(BARS, [("lim", 2, 0)], [("moc", 0, 0)], sigma=0.01)  # 2 sigma = 2 %: limit 9.80
    assert r["px"][0] == pytest.approx(9.8)
    assert engine.entry_name(("lim", 2, 0, 0.05)) == "lim2_abs5"
    assert engine.entry_name(("lim", 2, 0.05, 0.05)) == "lim2_0.05_abs5"
    assert engine.exit_name(("tgtstop", 0.05, 0.05)) == "tgt5_stop5"
    assert len({engine.entry_name(e) for e in engine.ENTRIES}) == len(engine.ENTRIES)


def test_tick_check_of_one_fill():
    ns = ticks.NS
    rec = {
        "date": "2020-07-01",
        "sym": "AAA",
        "t0": 0,
        "trades": [
            {"sip_timestamp": 5 * ns, "price": 9.9, "size": 100, "exchange": 11},
            {"sip_timestamp": 7 * ns, "price": 9.35, "size": 200, "exchange": 11},
            {"sip_timestamp": 8 * ns, "price": 9.30, "size": 50, "exchange": ticks.TRF},
        ],
        "quotes": [
            {"sip_timestamp": 6 * ns, "bid_price": 9.5, "bid_size": 1, "ask_price": 10.0, "ask_size": 1},
            {"sip_timestamp": 9 * ns, "bid_price": 9.2, "bid_size": 1, "ask_price": 9.87, "ask_size": 1},
        ],
    }
    c = ticks.check_fill(rec, limit=9.4, shares=53)
    assert c["prints_at_limit"] == 2 and c["lit_shares_at_limit"] == 200 and c["lit_covers_order"]
    assert c["bid_above_limit_before"] and c["spread_before"] == pytest.approx(10 / 9.5 - 1)
    assert c["ask_10s_vs_limit"] == pytest.approx(9.87 / 9.4 - 1) and "ask_1s_vs_limit" not in c
    assert ticks.check_fill({**rec, "trades": []}, 9.4, 53)["prints_at_limit"] == 0
