"""Stage-1 fill rule and statistics on hand-made rows."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from qts_rebound.stage1 import nw_t, trades


def _rows():
    return pd.DataFrame(
        {
            "date": pd.to_datetime(["2020-01-02"] * 4),
            "ticker": list("ABCD"),
            "c_prev": [100.0, 100.0, 100.0, 100.0],
            "sigma": [0.02] * 4,  # 2 sigma = 4 %: limit 96
            "o": [95.0, 99.0, 99.0, 99.0],  # A gaps through the limit
            "lo": [94.0, 95.4, 95.6, 90.0],  # B trades 0.62 % through, C only 0.42 %, D deep
            "c": [97.0, 97.0, 97.0, 92.0],
            "c_next": [98.0, 98.0, 98.0, 93.0],
        }
    )


def test_fill_rule_gap_at_open_and_trade_through():
    t = trades(_rows(), k=2.0, through=0.005).set_index("ticker")
    assert set(t.index) == {"A", "B", "D"}  # C touched 95.6 > 96 x 0.995 = 95.52: no fill
    assert t.loc["A", "gap_fill"] and t.loc["A", "fill"] == 95.0  # opening auction fill
    assert not t.loc["B", "gap_fill"] and math.isclose(t.loc["B", "fill"], 96.0)
    assert math.isclose(t.loc["B", "r"], 97 / 96 - 1)
    assert math.isclose(t.loc["D", "r"], 92 / 96 - 1)  # a falling knife is a loss
    nxt = trades(_rows(), k=2.0, through=0.005, exit_next=True).set_index("ticker")
    assert math.isclose(nxt.loc["B", "r"], 98 / 96 - 1)


def test_touch_only_counts_with_zero_trade_through():
    assert "C" in set(trades(_rows(), k=2.0, through=0.0)["ticker"])


def test_nw_t_lag0():
    x = np.arange(1.0, 13.0)
    _, t, n = nw_t(x, 0)
    assert n == 12 and math.isclose(t, 6.5 / math.sqrt(((x - 6.5) ** 2).mean() / 12))
