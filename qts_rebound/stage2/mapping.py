"""Match Sharadar stock-days to the symbol Massive files them under (pre-registered rule).

Massive keys bars by the symbol traded that day; Sharadar files a company's whole history under
its current ticker and suffixes reused tickers with digits (``ABC1``). The rule
(docs/notes/2026-11-14_preregistration_stage2.md, "Matching"):

1. candidate symbols: the ticker and every ``relatedtickers`` entry, trailing digits removed,
   class separator "." also written "-", "/" or nothing;
2. accept a candidate whose Massive daily bar closes within 0.5 % of Sharadar's as-traded close
   with volume within a factor of 2 of Sharadar's as-traded volume; closest close wins (ties:
   a symbol derived from the ticker itself, then the closer volume);
3. otherwise the unique Massive symbol that day with close within 0.1 % and volume within a
   factor of 1.25; no unique match: unmatched.

Only prices and volumes of day D enter; no return is computed here.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path

import duckdb
import pandas as pd

SEPARATORS = (".", "-", "/", "")
DAY_COLUMNS = {
    "ticker": "VARCHAR",
    "volume": "DOUBLE",
    "open": "DOUBLE",
    "close": "DOUBLE",
    "high": "DOUBLE",
    "low": "DOUBLE",
    "window_start": "BIGINT",
    "transactions": "BIGINT",
}
ALIAS_CLOSE_TOL = 0.005
ALIAS_VOL_FACTOR = 2.0
PRICE_CLOSE_TOL = 0.001
PRICE_VOL_FACTOR = 1.25


def strip_digits(t: str) -> str:
    return re.sub(r"\d+$", "", t or "")


def symbol_forms(base: str) -> list[str]:
    if "." not in base:
        return [base]
    return [base.replace(".", s) for s in SEPARATORS]


def alias_table(security_master: pd.DataFrame) -> pd.DataFrame:
    """(ticker, sym, own): every Massive symbol a Sharadar ticker may have traded under;
    ``own`` marks forms derived from the ticker itself rather than from a related ticker."""
    rows: list[tuple[str, str, bool]] = []
    for t, rel in zip(security_master["ticker"], security_master["relatedtickers"], strict=True):
        if not isinstance(t, str) or not t:
            continue
        seen: set[str] = set()
        own_base = strip_digits(t)
        related = rel.split() if isinstance(rel, str) else []
        for base, own in [(own_base, True)] + [(strip_digits(r), False) for r in related]:
            if not base:
                continue
            for f in symbol_forms(base):
                if f and f not in seen:
                    seen.add(f)
                    rows.append((t, f, own))
    return pd.DataFrame(rows, columns=["ticker", "sym", "own"])


def day_bars_sql(files: Sequence[Path]) -> str:
    cols = ", ".join(f"'{k}': '{v}'" for k, v in DAY_COLUMNS.items())
    lst = ", ".join("'" + str(p).replace("'", "''") + "'" for p in files)
    return (
        "select regexp_extract(filename, '(\\d{4}-\\d{2}-\\d{2})\\.csv\\.gz$', 1)::date as date, "
        "ticker as sym, close, volume "
        f"from read_csv([{lst}], header = true, filename = true, columns = {{{cols}}})"
    )


def match(
    cands: pd.DataFrame,
    alias: pd.DataFrame,
    day_files: Sequence[Path],
    con: duckdb.DuckDBPyConnection | None = None,
) -> pd.DataFrame:
    """One row per matched (date, ticker): sym, method ('alias' or 'price'), rel (close
    difference), vr (volume ratio). ``cands`` needs date, ticker, cu (as-traded close), c
    (split-adjusted close) and v (split-adjusted volume) for the days the files cover."""
    own = con is None
    con = con or duckdb.connect()
    try:
        c = cands[["date", "ticker", "cu", "c", "v"]].drop_duplicates(["date", "ticker"]).copy()
        c["date"] = pd.to_datetime(c["date"]).dt.date
        con.register("cand_in", c)
        con.register("alias_in", alias)
        con.execute(f"create or replace temp table mday as {day_bars_sql(day_files)}")
        q = f"""
        with c as (
            select date, ticker, cu, v * c / cu as v_raw from cand_in where cu > 0 and c > 0 and v > 0),
        a as (select c.*, al.sym, al.own from c join alias_in al using (ticker)),
        m1 as (
            select a.date, a.ticker, a.sym, a.own, abs(m.close / a.cu - 1) rel, m.volume / a.v_raw vr
            from a join mday m on m.date = a.date and m.sym = a.sym
            where m.close > 0 and m.volume > 0),
        ok1 as (
            select * from m1
            where rel <= {ALIAS_CLOSE_TOL} and vr between {1 / ALIAS_VOL_FACTOR} and {ALIAS_VOL_FACTOR}),
        best1 as (
            select date, ticker, sym, rel, vr from (
                select *, row_number() over (
                    partition by date, ticker order by rel, own desc, abs(ln(vr)), sym) rn
                from ok1)
            where rn = 1),
        un as (select c.* from c anti join best1 using (date, ticker)),
        m2 as (
            select un.date, un.ticker, m.sym, abs(m.close / un.cu - 1) rel, m.volume / un.v_raw vr
            from un join mday m on m.date = un.date
                and m.close between un.cu * {1 - PRICE_CLOSE_TOL} and un.cu * {1 + PRICE_CLOSE_TOL}
            where m.volume between un.v_raw / {PRICE_VOL_FACTOR} and un.v_raw * {PRICE_VOL_FACTOR}),
        uniq2 as (
            select date, ticker, min(sym) sym, min(rel) rel, min(vr) vr
            from m2 group by date, ticker having count(*) = 1)
        select date, ticker, sym, 'alias' as method, rel, vr from best1
        union all
        select date, ticker, sym, 'price' as method, rel, vr from uniq2
        order by date, ticker"""
        out = con.execute(q).df()
    finally:
        con.execute("drop table if exists mday")
        con.unregister("cand_in")
        con.unregister("alias_in")
        if own:
            con.close()
    out["date"] = pd.to_datetime(out["date"])
    return out


def coverage(cands: pd.DataFrame, matched: pd.DataFrame) -> pd.DataFrame:
    """Matched share per calendar year (and overall, year 0) of the distinct stock-days."""
    c = cands[["date", "ticker"]].drop_duplicates().copy()
    c["date"] = pd.to_datetime(c["date"])
    m = matched[["date", "ticker"]].assign(hit=True)
    x = c.merge(m, on=["date", "ticker"], how="left")
    x["hit"] = x["hit"].fillna(False).astype(bool)
    by = x.groupby(x["date"].dt.year)["hit"].agg(["size", "sum"])
    by.loc[0] = [len(x), int(x["hit"].sum())]
    by.columns = ["stock_days", "matched"]
    by["share"] = by["matched"] / by["stock_days"]
    return by.sort_index()
