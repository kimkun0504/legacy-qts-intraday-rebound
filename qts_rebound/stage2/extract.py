"""Cut the minute files down to the stock-days stage 2 needs, plus IWM and SPY every day.

One parquet file per month (``data/stage2/minute/YYYY-MM.parquet``) with bars from 04:00 to
15:59 New York time: date, sym, minute (minutes after midnight, New York), o, h, l, c, v, n.
Resumable: a month whose output exists is skipped. Only research files are read
(:func:`qts_rebound.stage2.data.research_files`).
"""

from __future__ import annotations

import datetime as dt
import os
from collections.abc import Iterable, Sequence
from pathlib import Path

import duckdb
import pandas as pd

from qts_rebound.stage2.data import CLOSE, PRE_OPEN, utc_offset_minutes

MINUTE_COLUMNS = {
    "ticker": "VARCHAR",
    "volume": "DOUBLE",
    "open": "DOUBLE",
    "close": "DOUBLE",
    "high": "DOUBLE",
    "low": "DOUBLE",
    "window_start": "BIGINT",
    "transactions": "BIGINT",
}
ALWAYS = ("IWM", "SPY")
NS_PER_MINUTE = 60_000_000_000


def _file_list(files: Sequence[Path]) -> str:
    return ", ".join("'" + str(p).replace("'", "''") + "'" for p in files)


def extract_files(
    files: Sequence[tuple[dt.date, Path]],
    wanted: pd.DataFrame,
    out: Path,
    always: Iterable[str] = ALWAYS,
    threads: int = 4,
    memory_limit: str = "2GB",
) -> int:
    """Writes the bars of ``wanted`` (date, sym) and of the ``always`` symbols found in ``files``
    to ``out`` (parquet). Returns the row count."""
    if not files:
        return 0
    offs = pd.DataFrame({"date": [d for d, _ in files], "off": [utc_offset_minutes(d) for d, _ in files]})
    w = wanted[["date", "sym"]].drop_duplicates().copy()
    w["date"] = pd.to_datetime(w["date"]).dt.date
    al = pd.DataFrame({"sym": list(always)})
    cols = ", ".join(f"'{k}': '{v}'" for k, v in MINUTE_COLUMNS.items())
    con = duckdb.connect()
    try:
        con.execute(f"set threads = {threads}")
        con.execute(f"set memory_limit = '{memory_limit}'")
        con.execute("set preserve_insertion_order = false")
        tmpdir = os.environ.get("TMPDIR", "/tmp")
        con.execute(f"set temp_directory = '{tmpdir}/duckdb_spill'")
        con.register("w_in", w)
        con.register("offs_in", offs)
        con.register("always_in", al)
        tmp = out.with_name(out.name + ".tmp")
        out.parent.mkdir(parents=True, exist_ok=True)
        q = f"""
        copy (
          with f as (
            select regexp_extract(filename, '(\\d{{4}}-\\d{{2}}-\\d{{2}})\\.csv\\.gz$', 1)::date as date,
                   ticker as sym, open o, high h, low l, close c, volume v, transactions n, window_start ws
            from read_csv([{_file_list([p for _, p in files])}], header = true, filename = true,
                          columns = {{{cols}}})),
          k as (
            select f.* from f semi join (select date, sym from w_in union select o.date, a.sym
                                         from offs_in o cross join always_in a) s
            on s.date = f.date and s.sym = f.sym)
          select k.date, k.sym, cast(((k.ws // {NS_PER_MINUTE}) + o.off) % 1440 as smallint) as minute,
                 k.o, k.h, k.l, k.c, k.v, cast(k.n as integer) as n
          from k join offs_in o on o.date = k.date
          where ((k.ws // {NS_PER_MINUTE}) + o.off) % 1440 between {PRE_OPEN} and {CLOSE - 1}
          order by k.sym, k.date, minute
        ) to '{tmp}' (format parquet, compression zstd)"""
        con.execute(q)
        n = con.execute(f"select count(*) from read_parquet('{tmp}')").fetchone()[0]
    finally:
        con.close()
    os.replace(tmp, out)
    return int(n)


def months(files: Sequence[tuple[dt.date, Path]]) -> dict[str, list[tuple[dt.date, Path]]]:
    by: dict[str, list[tuple[dt.date, Path]]] = {}
    for d, p in files:
        by.setdefault(f"{d:%Y-%m}", []).append((d, p))
    return by
