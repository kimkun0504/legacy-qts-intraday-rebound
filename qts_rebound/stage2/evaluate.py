"""Stage-2 statistics (pre-registered): costs, the daily P&L sum, Newey-West t, folds, the
capped book, Holm and the four pass rules."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

from qts_rebound.stage1 import nw_t

PER_DAY_CAP = 20
NW_LAG = 5
N_FOLDS = 6
MIN_T = 3.0
MIN_NET = 0.003
HOLM_ALPHA = 0.05


def costs(t: pd.DataFrame) -> pd.Series:
    """Round trip as a fraction of the filled position: IBKR Tiered fees for one buy order of
    the filled shares, the auction exchange fee on its auction part, and an MOC sell through the
    small-account cost model (fees + auction impact)."""
    from qts.core.config import CostConfig
    from qts.tcost.fees import FeeSchedule
    from qts_smallcap.costs.model import SmallAccountConfig, SmallAccountCostModel

    fees = FeeSchedule(CostConfig())
    cm = SmallAccountCostModel()
    auction_fee = SmallAccountConfig().auction_exchange_fee_per_share
    out = []
    for d, g in t.groupby("date"):
        idx = g.index
        sh = g["shares"].to_numpy(dtype=float)
        px = g["fill_px"].to_numpy(dtype=float)
        buy = (
            fees.explicit_cost_vec(sh, px, False, d, is_auction=False)
            + g["q_auction"].to_numpy() * auction_fee
        )
        sell = cm.order_costs(
            pd.Series(-sh, index=idx),
            pd.Series(g["cu"].to_numpy(dtype=float), index=idx),
            pd.Series(g["adv_prev"].fillna(1e6).to_numpy(), index=idx),
            pd.Series(g["sigma"].to_numpy(), index=idx),
            date=d,
        )["total"].to_numpy()
        out.append(pd.Series((buy + sell) / (sh * px), index=idx))
    return pd.concat(out).reindex(t.index) if out else pd.Series(dtype=float)


def daily_pnl(t: pd.DataFrame, days: pd.DatetimeIndex, col: str = "net") -> pd.Series:
    """S_d: the day's summed returns in units of one position, 0 on days without fills."""
    s = t.groupby(pd.to_datetime(t["date"]))[col].sum()
    return s.reindex(days, fill_value=0.0)


def capped(t: pd.DataFrame, per_day: int = PER_DAY_CAP) -> pd.DataFrame:
    """The first ``per_day`` fills of each day by entry time; ties: higher 63-day ADV, then ticker."""
    x = t.sort_values(["date", "entry_minute", "adv_prev", "ticker"], ascending=[True, True, False, True])
    return x.groupby("date", sort=False).head(per_day)


def folds_positive(s: pd.Series, n: int = N_FOLDS) -> int:
    return int(sum(float(np.sum(f)) > 0 for f in np.array_split(s.to_numpy(), n) if len(f)))


def holm(ps: list[float]) -> list[float]:
    order = sorted(range(len(ps)), key=lambda i: ps[i])
    adj, running = [0.0] * len(ps), 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (len(ps) - rank) * ps[i]))
        adj[i] = running
    return adj


@dataclass
class Stage2Result:
    trial: str
    trades: int
    trading_days: int
    days_with_fills: int
    fills_per_day: float
    gross_mean: float
    gross_median: float
    win_rate: float
    net_mean: float
    cost_median: float
    daily_pnl_mean: float
    nw_t: float
    p_two_sided: float
    folds_positive: int
    capped_trades: int
    capped_net_mean: float
    kinds: dict = field(default_factory=dict)
    by_year_net: dict = field(default_factory=dict)
    holm_p: float = float("nan")
    pass_t: bool = False
    pass_net: bool = False
    pass_folds: bool = False
    pass_capped: bool = False
    pass_all: bool = False


def evaluate(name: str, t: pd.DataFrame, days: pd.DatetimeIndex) -> Stage2Result:
    """``t`` needs date, ticker, r, net, cost, kind, entry_minute, adv_prev."""
    t = t[np.isfinite(t["net"])]
    s = daily_pnl(t, days)
    m, tt, _ = nw_t(s.to_numpy(), NW_LAG)
    p = float(math.erfc(abs(tt) / math.sqrt(2))) if math.isfinite(tt) else float("nan")
    cp = capped(t)
    kinds = {
        k: {
            "share": float(len(g) / max(len(t), 1)),
            "gross_mean": float(g["r"].mean()),
            "net_mean": float(g["net"].mean()),
        }
        for k, g in t.groupby("kind")
    }
    by_year = {
        str(y): round(float(v), 5)
        for y, v in t.groupby(pd.to_datetime(t["date"]).dt.year)["net"].mean().items()
    }
    n_days = int(t["date"].nunique())
    return Stage2Result(
        trial=name,
        trades=len(t),
        trading_days=len(days),
        days_with_fills=n_days,
        fills_per_day=len(t) / max(n_days, 1),
        gross_mean=float(t["r"].mean()),
        gross_median=float(t["r"].median()),
        win_rate=float((t["r"] > 0).mean()),
        net_mean=float(t["net"].mean()),
        cost_median=float(t["cost"].median()),
        daily_pnl_mean=m,
        nw_t=tt,
        p_two_sided=p,
        folds_positive=folds_positive(s),
        capped_trades=len(cp),
        capped_net_mean=float(cp["net"].mean()) if len(cp) else float("nan"),
        kinds=kinds,
        by_year_net=by_year,
    )


def verdicts(results: list[Stage2Result]) -> list[Stage2Result]:
    """Holm over the trials, then the four pass rules (all must hold)."""
    adj = holm([r.p_two_sided for r in results])
    for r, a in zip(results, adj, strict=True):
        r.holm_p = a
        r.pass_t = bool(r.nw_t >= MIN_T and a < HOLM_ALPHA)
        r.pass_net = bool(r.net_mean >= MIN_NET)
        r.pass_folds = bool(r.folds_positive >= 4)
        r.pass_capped = bool(r.capped_net_mean > 0)
        r.pass_all = r.pass_t and r.pass_net and r.pass_folds and r.pass_capped
    return results


def as_dict(r: Stage2Result) -> dict:
    return asdict(r)


def premarket_dollar_volume(bars: pd.DataFrame, first: int, last: int) -> pd.DataFrame:
    """date, sym, pre_dv: sum of volume x close over bars starting in [first, last]."""
    b = bars[(bars["minute"] >= first) & (bars["minute"] <= last)]
    out = (b["v"] * b["c"]).groupby([pd.to_datetime(b["date"]), b["sym"]]).sum()
    return out.rename("pre_dv").reset_index().rename(columns={"level_0": "date"})
