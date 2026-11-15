"""The pre-registered minute-bar fill model (docs/notes/2026-11-14_preregistration_stage2.md).

Orders: buy limit L_D = C(D-1) x (1 - 2 sigma) x closeunadj(D) / close(D), q = floor($500 / L_D)
shares, cancelled at the cut-off (15:45, or 12:45 on early-close days).

Over the regular-hours bars of D that start before the cut-off:
- opening auction: if the first bar starts at 09:30 and opens at or below L_D, q_a =
  min(q, floor(0.25 x V_1)) shares fill at O_1;
- continuous: the remaining shares fill at L_D at the end of the first bar by which some bar has
  traded at or below L_D x 0.995 and at least 2x the remaining shares have traded in bars whose
  low is at or below L_D;
- exit at the official close (``cu`` = Sharadar ``closeunadj``).

Everything is vectorised over orders; the parameters exist for the pre-registered diagnostics.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from qts_rebound.stage2.data import CLOSE, CUTOFF_BEFORE_CLOSE, EARLY_CLOSE, OPEN

POSITION_USD = 500.0
K_SIGMA = 2.0


@dataclass(frozen=True)
class FillParams:
    through: float = 0.005  # the low must reach L_D x (1 - through)
    alpha: float = 0.25  # largest share of the first minute's volume the auction part may take
    vol_mult: float = 2.0  # shares traded at or below L_D needed, as a multiple of the order
    auction_at_limit: bool = False  # diagnostic: price the auction part at L_D instead of O_1
    cutoff: int | None = None  # diagnostic: an earlier cut-off (minute after midnight)


def orders_table(
    cands: pd.DataFrame, mapping: pd.DataFrame, k: float = K_SIGMA, position_usd: float = POSITION_USD
) -> pd.DataFrame:
    """One order per matched candidate stock-day, with the limit and the share count as traded."""
    x = cands.copy()
    x["date"] = pd.to_datetime(x["date"])
    m = mapping[["date", "ticker", "sym"]].copy()
    m["date"] = pd.to_datetime(m["date"])
    x = x.merge(m, on=["date", "ticker"], how="inner")
    x["limit_adj"] = x["c_prev"] * (1 - k * x["sigma"])
    x = x[(x["limit_adj"] > 0) & (x["c"] > 0) & (x["cu"] > 0)].copy()
    x["factor"] = x["cu"] / x["c"]
    x["limit"] = x["limit_adj"] * x["factor"]
    x["q"] = np.maximum(np.floor(position_usd / x["limit"]), 1.0)
    return x.reset_index(drop=True)


def sessions(iwm: pd.DataFrame) -> pd.DataFrame:
    """date, close (minute) and cut-off per day from IWM's bars: a day without an IWM
    regular-hours bar starting at or after 13:00 closed early (13:00)."""
    b = iwm[(iwm["minute"] >= OPEN) & (iwm["minute"] < CLOSE)]
    last = b.groupby("date")["minute"].max()
    close = np.where(last.to_numpy() >= EARLY_CLOSE, CLOSE, EARLY_CLOSE)
    out = pd.DataFrame({"date": pd.to_datetime(last.index), "close": close})
    out["cutoff"] = out["close"] - CUTOFF_BEFORE_CLOSE
    return out


def simulate(
    orders: pd.DataFrame, bars: pd.DataFrame, sess: pd.DataFrame, p: FillParams | None = None
) -> pd.DataFrame:
    """Adds to each order: q_auction, q_cont, shares, fill_px, entry_minute, cont_minute (start
    of the bar that completed the continuous part), first_minute, kind and filled. Orders on days
    missing from ``sess`` are treated as normal days."""
    p = p or FillParams()
    o = orders.reset_index(drop=True).copy()
    o["oid"] = np.arange(len(o))
    s = sess[["date", "close", "cutoff"]].copy()
    s["date"] = pd.to_datetime(s["date"])
    o = o.drop(columns=[c for c in ("close", "cutoff") if c in o.columns]).merge(s, on="date", how="left")
    o = o.sort_values("oid").reset_index(drop=True)
    o["close"] = o["close"].fillna(CLOSE)
    o["cutoff"] = o["cutoff"].fillna(CLOSE - CUTOFF_BEFORE_CLOSE)
    if p.cutoff is not None:
        o["cutoff"] = np.minimum(o["cutoff"], p.cutoff)

    b = bars[["date", "sym", "minute", "o", "l", "v"]].copy()
    b["date"] = pd.to_datetime(b["date"])
    b = b.merge(o[["date", "sym", "oid", "limit", "cutoff"]], on=["date", "sym"], how="inner")
    b = b[(b["minute"] >= OPEN) & (b["minute"] < b["cutoff"])].sort_values(["oid", "minute"], kind="stable")

    n = len(o)
    first = b.groupby("oid", sort=False).head(1).set_index("oid")
    m1 = first["minute"].reindex(range(n)).to_numpy(dtype=float)
    o1 = first["o"].reindex(range(n)).to_numpy(dtype=float)
    v1 = first["v"].reindex(range(n)).to_numpy(dtype=float)
    lim = o["limit"].to_numpy(dtype=float)
    q = o["q"].to_numpy(dtype=float)

    auction = (m1 == OPEN) & (o1 <= lim)
    q_a = np.where(auction, np.minimum(q, np.floor(p.alpha * np.nan_to_num(v1))), 0.0)
    need = q - q_a

    oid = b["oid"].to_numpy()
    bl = b["l"].to_numpy(dtype=float)
    blim = lim[oid]
    touch_v = np.where(bl <= blim, b["v"].to_numpy(dtype=float), 0.0)
    through = bl <= blim * (1 - p.through)
    g = pd.Series(oid)
    cum_touch = pd.Series(touch_v).groupby(g.values).cumsum().to_numpy()
    seen = pd.Series(through).groupby(g.values).cummax().to_numpy(dtype=bool)
    bneed = need[oid]
    ok = seen & (cum_touch >= p.vol_mult * bneed) & (bneed > 0)
    cont = pd.Series(b["minute"].to_numpy()[ok]).groupby(oid[ok]).first()
    cont_minute = cont.reindex(range(n)).to_numpy(dtype=float)

    q_c = np.where((need > 0) & np.isfinite(cont_minute), need, 0.0)
    shares = q_a + q_c
    auc_px = lim if p.auction_at_limit else o1
    with np.errstate(invalid="ignore", divide="ignore"):
        fill_px = np.where(shares > 0, (q_a * np.nan_to_num(auc_px) + q_c * lim) / shares, np.nan)
    entry = np.where(q_a > 0, OPEN, cont_minute + 1)
    kind = np.select(
        [(q_a > 0) & (q_c > 0), q_a > 0, q_c > 0], ["auction+continuous", "auction", "continuous"], "none"
    )
    o["first_minute"] = m1
    o["q_auction"] = q_a
    o["q_cont"] = q_c
    o["shares"] = shares
    o["fill_px"] = fill_px
    o["entry_minute"] = np.where(shares > 0, entry, np.nan)
    o["cont_minute"] = np.where(q_c > 0, cont_minute, np.nan)
    o["kind"] = kind
    o["filled"] = shares > 0
    return o


def trades(filled_orders: pd.DataFrame) -> pd.DataFrame:
    """Filled orders with the gross return to the official close."""
    t = filled_orders[filled_orders["filled"]].copy()
    t["r"] = t["cu"] / t["fill_px"] - 1.0
    return t[np.isfinite(t["r"])].copy()
