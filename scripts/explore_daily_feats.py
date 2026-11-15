"""Daily features known before D's open for every micro candidate stock-day (exploratory search).
Written to data/explore/daily_feats.parquet. Only rows up to D-1 enter each feature."""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))
import run_stage2 as s2  # noqa: E402

STORE = REPO.parent / "quant-trading-system" / "data" / "store"
OUT = REPO / "data" / "explore" / "daily_feats.parquet"


def main() -> None:
    cands = s2.micro_candidates(no_news_only=False)[["date", "ticker"]]
    con = duckdb.connect(str(s2.PANEL_DB), read_only=True)
    con.register("cands", cands)
    con.execute(
        f"create temp view vol as select date, ticker, value v from read_parquet('{STORE}/prices_volume/*/*.parquet')"
    )
    con.execute(
        f"create temp view sm as select ticker, any_value(sicsector) sector, any_value(category) category, min(firstpricedate) first_date from read_parquet('{STORE}/security_master/all.parquet') group by 1"
    )
    q = """
    with tk as (select distinct ticker from cands),
    p as (select px.date, px.ticker, px.c, px.mcap, vol.v
          from px join tk using (ticker) left join vol on vol.date = px.date and vol.ticker = px.ticker),
    f as (select date, ticker,
            lag(c, 1) over w / lag(c, 2) over w - 1 as ret1,
            lag(c, 1) over w / lag(c, 6) over w - 1 as ret5,
            lag(c, 1) over w / lag(c, 21) over w - 1 as ret20,
            lag(c, 1) over w / lag(c, 61) over w - 1 as ret60,
            lag(c, 1) over w / max(c) over (partition by ticker order by date rows between 60 preceding and 1 preceding) - 1 as dd60,
            lag(v, 1) over w / nullif(avg(v) over (partition by ticker order by date rows between 63 preceding and 2 preceding), 0) as volr1,
            avg(v) over (partition by ticker order by date rows between 5 preceding and 1 preceding)
              / nullif(avg(v) over (partition by ticker order by date rows between 63 preceding and 6 preceding), 0) as volr5,
            lag(mcap, 1) over w as mcap
          from p window w as (partition by ticker order by date))
    select c.date, c.ticker, f.* exclude (date, ticker), sm.sector, sm.category,
           date_diff('day', sm.first_date, c.date) as age_days
    from cands c left join f on f.date = c.date and f.ticker = c.ticker left join sm on sm.ticker = c.ticker
    """
    df = con.execute(q).df()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT, index=False)
    print(len(df), df.notna().mean().round(3).to_dict())


if __name__ == "__main__":
    main()
