"""Stage 1 of the intraday rebound (docs/notes/2026-11-14_preregistration_stage1.md).

``candidates`` builds, point in time, every universe name-day whose low reached 1.5 sigma below
the prior close (a superset of every rule tested here) with what the rules need; ``trades``
applies one rule (threshold, trade-through, news subset) and prices each trade at the sleeve's
real size; ``evaluate`` gives the pre-registered statistics and the diagnostics.

Timing: everything that decides the order (universe, sigma, the limit, the news flag) is known
before D's open: the universe is the last formation Friday before D, sigma ends at D-1, news is
an earnings date in [D-1, D] (scheduled) or an 8-K dated on or after D-1's date and before D's
(a filing made the evening before or over a weekend); 8-Ks dated D are not used.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

import duckdb
import numpy as np
import pandas as pd
from qts.core.config import CostConfig
from qts.tcost.fees import FeeSchedule
from qts_smallcap.costs.model import SmallAccountCostModel

START = "2003-06-02"
SIGMA_ROWS = 60
WIDEST_K = 1.5
POSITION_USD = 500.0


def candidates(db: str, sc_store: str, shared: str, load_end: str, uni: str = "uni") -> pd.DataFrame:
    con = duckdb.connect(db, read_only=True)
    con.execute("set enable_progress_bar = false")
    con.execute("set threads = 3")
    w = f"date between date '{START}' and date '{load_end}'"
    q = f"""
    with fri as (select date fdate, d fd from cal where friday),
    days as (select date, d from cal where {w}),
    dmap as (select days.date, days.d, f.fdate from days asof join fri f on f.fd <= days.d - 1),
    mem as (select dm.date, dm.d, u.ticker from dmap dm join {uni} u on u.date = dm.fdate),
    sig as (
        select ticker, d,
               stddev_samp(r) over (partition by ticker order by d rows between {SIGMA_ROWS} preceding and 1 preceding) sigma,
               count(r) over (partition by ticker order by d rows between {SIGMA_ROWS} preceding and 1 preceding) n_sig,
               lag(adv) over (partition by ticker order by d) adv_prev
        from (select ticker, d, adv, c / lag(c) over (partition by ticker order by d) - 1 r from px2)),
    sep as (
        select s.ticker, c.d, s.open o, s.low lo, s.close cl, s.closeunadj cu, s.volume v
        from read_parquet('{sc_store}/sep_ohlc/year=*/*.parquet') s join cal c on c.date = s.date
        where s.date between date '{START}' - interval 10 day and date '{load_end}'),
    x as (
        select m.date, m.d, m.ticker, cp.date prev_date, p.cl c_prev, t.o, t.lo, t.cl c, t.cu, t.v, nx.cl c_next,
               g.sigma, g.adv_prev
        from mem m
        join sig g on g.ticker = m.ticker and g.d = m.d and g.n_sig >= 40
        join cal cp on cp.d = m.d - 1
        join sep p on p.ticker = m.ticker and p.d = m.d - 1
        join sep t on t.ticker = m.ticker and t.d = m.d
        left join sep nx on nx.ticker = m.ticker and nx.d = m.d + 1
        where t.v > 0 and t.lo > 0 and p.cl > 0 and t.lo <= p.cl * (1 - {WIDEST_K} * g.sigma))
    select * from x"""
    df = con.execute(q).df()
    con.close()
    df["date"] = pd.to_datetime(df["date"])
    df["prev_date"] = pd.to_datetime(df["prev_date"])
    return df


def news_flags(cands: pd.DataFrame, shared: str) -> np.ndarray:
    """True where news was known before D's open: an earnings announcement dated from the
    previous trading day through D (scheduled), or an 8-K dated from the previous trading day
    up to, not including, D (filed the evening before or over a weekend)."""
    con = duckdb.connect()
    con.execute("set enable_progress_bar = false")
    con.register("c", cands[["date", "prev_date", "ticker"]])
    q = f"""
    with e as (select ticker, date ann from read_parquet('{shared}/earnings_dates/year=*/*.parquet')),
    k as (select ticker, date ev from read_parquet('{shared}/sharadar_events/year=*/*.parquet')),
    en as (select distinct c.date, c.ticker from c join e
           on e.ticker = c.ticker and e.ann >= c.prev_date and e.ann <= c.date),
    kn as (select distinct c.date, c.ticker from c join k
           on k.ticker = c.ticker and k.ev >= c.prev_date and k.ev < c.date)
    select c.date, c.ticker, (en.ticker is not null) earn, (kn.ticker is not null) eightk
    from c left join en using (date, ticker) left join kn using (date, ticker)"""
    f = con.execute(q).df()
    con.close()
    f["date"] = pd.to_datetime(f["date"])
    m = cands[["date", "ticker"]].merge(f, on=["date", "ticker"], how="left")
    return (m["earn"].fillna(False) | m["eightk"].fillna(False)).to_numpy(dtype=bool)


def trades(
    cands: pd.DataFrame, k: float = 2.0, through: float = 0.005, exit_next: bool = False
) -> pd.DataFrame:
    """Fills of the rule ``limit = C(D-1) x (1 - k sigma)``: at the open if it gaps through,
    else at the limit when ``low <= limit x (1 - through)``; return to the close (or next close)."""
    x = cands.copy()
    x["limit"] = x["c_prev"] * (1 - k * x["sigma"])
    x = x[x["limit"] > 0]
    gap = x["o"] <= x["limit"]
    hit = gap | (x["lo"] <= x["limit"] * (1 - through))
    x = x[hit].copy()
    x["gap_fill"] = gap[hit]
    x["fill"] = np.where(x["gap_fill"], x["o"], x["limit"])
    exit_px = x["c_next"] if exit_next else x["c"]
    x["r"] = exit_px / x["fill"] - 1.0
    x = x[x["r"].notna()]
    return x


def costs(t: pd.DataFrame, position_usd: float = POSITION_USD) -> pd.Series:
    """Round trip as a fraction of the position: passive limit buy (fees only), MOC sell."""
    fees = FeeSchedule(CostConfig())
    cm = SmallAccountCostModel()
    out = []
    for d, g in t.groupby("date"):
        factor = (g["cu"] / g["c"]).to_numpy()
        fill_raw = g["fill"].to_numpy() * factor
        close_raw = g["cu"].to_numpy()
        q = np.round(position_usd / fill_raw)
        q = np.where(q < 1, np.nan, q)
        buy = fees.explicit_cost_vec(np.nan_to_num(q), fill_raw, False, d, is_auction=False)
        idx = g.index
        sell = cm.order_costs(
            pd.Series(-np.nan_to_num(q), index=idx),
            pd.Series(close_raw, index=idx),
            pd.Series(g["adv_prev"].fillna(1e6).to_numpy(), index=idx),
            pd.Series(g["sigma"].to_numpy(), index=idx),
            date=d,
        )["total"].to_numpy()
        out.append(pd.Series((buy + sell) / (q * fill_raw), index=idx))
    return pd.concat(out).reindex(t.index)


def nw_t(x: np.ndarray, lag: int) -> tuple[float, float, int]:
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    n = len(x)
    if n < 10:
        return float("nan"), float("nan"), n
    e = x - x.mean()
    v = e @ e / n
    for j in range(1, min(lag, n - 1) + 1):
        v += 2 * (1 - j / (lag + 1)) * (e[j:] @ e[:-j]) / n
    return float(x.mean()), float(x.mean() / math.sqrt(v / n)), n


@dataclass
class Stage1Result:
    trial: str
    days_with_fills: int
    trades: int
    fills_per_day: float
    gross_daily_mean: float
    nw_t: float
    p_two_sided: float
    pass_t: bool
    net_per_trade: float
    pass_net: bool
    folds_positive: int
    pass_folds: bool
    raw_gain_mean: float
    raw_gain_median: float
    win_rate: float
    cost_median: float
    gap_fill_share: float
    gap_fill_mean: float
    intraday_fill_mean: float
    minus_iwm_mean: float | None = None
    by_year: dict = field(default_factory=dict)


def evaluate(name: str, t: pd.DataFrame, cost: pd.Series, iwm_oc: pd.Series | None = None) -> Stage1Result:
    t = t.assign(cost=cost)
    t = t[t["cost"].notna()]
    daily = t.groupby("date")["r"].mean()
    m, tt, n = nw_t(daily.to_numpy(), 5)
    folds = np.array_split(daily.to_numpy(), 6)
    fp = int(sum(np.nanmean(f) > 0 for f in folds if len(f)))
    net = t["r"] - t["cost"]
    hedged = None
    if iwm_oc is not None:
        hedged = float((t["r"] - t["date"].map(iwm_oc)).mean())
    return Stage1Result(
        trial=name,
        days_with_fills=n,
        trades=len(t),
        fills_per_day=len(t) / max(n, 1),
        gross_daily_mean=m,
        nw_t=tt,
        p_two_sided=float(math.erfc(abs(tt) / math.sqrt(2))) if math.isfinite(tt) else float("nan"),
        pass_t=bool(tt >= 3.0),
        net_per_trade=float(net.mean()),
        pass_net=bool(net.mean() > 0),
        folds_positive=fp,
        pass_folds=fp >= 4,
        raw_gain_mean=float(t["r"].mean()),
        raw_gain_median=float(t["r"].median()),
        win_rate=float((t["r"] > 0).mean()),
        cost_median=float(t["cost"].median()),
        gap_fill_share=float(t["gap_fill"].mean()),
        gap_fill_mean=float(t.loc[t["gap_fill"], "r"].mean()) if t["gap_fill"].any() else float("nan"),
        intraday_fill_mean=float(t.loc[~t["gap_fill"], "r"].mean())
        if (~t["gap_fill"]).any()
        else float("nan"),
        minus_iwm_mean=hedged,
        by_year={str(y): round(float(v), 5) for y, v in daily.groupby(daily.index.year).mean().items()},
    )


def as_dict(r: Stage1Result) -> dict:
    return asdict(r)
