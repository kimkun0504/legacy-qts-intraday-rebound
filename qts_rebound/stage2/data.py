"""Paths, the seal guard and time helpers for the Massive files.

Research code reads Massive files only through :func:`research_files`, which lists
``data/massive/raw`` and stops at the day before the seal's ``holdout_start``. Holdout files live
in ``data/massive/sealed`` (the downloader puts them there) and are never listed here; a raw file
dated inside the holdout is treated as a misplaced file and raises.
"""

from __future__ import annotations

import datetime as dt
import os
import re
from functools import cache
from pathlib import Path
from zoneinfo import ZoneInfo

from qts_rebound.massive.flatfiles import DATASETS, read_holdout_start
from qts_rebound.paths import REPO_ROOT, SEAL_FILE

MASSIVE_ROOT = Path(os.environ.get("QTS_MASSIVE_ROOT", REPO_ROOT / "data" / "massive"))
STAGE2 = Path(os.environ.get("QTS_STAGE2_OUT", REPO_ROOT / "data" / "stage2"))
WINDOW_START = dt.date(2003, 9, 10)
NY = ZoneInfo("America/New_York")
FILE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})\.csv\.gz$")

#: minutes after midnight, New York time
PRE_OPEN = 4 * 60  # 04:00
PRE_LAST = 9 * 60 + 24  # last pre-market bar used by P3 starts 09:24
OPEN = 9 * 60 + 30  # 09:30
CLOSE = 16 * 60  # 16:00
EARLY_CLOSE = 13 * 60  # 13:00
CUTOFF_BEFORE_CLOSE = 15  # orders cancelled 15 minutes before the close


class SealViolation(RuntimeError):
    pass


def holdout_start() -> dt.date:
    return read_holdout_start(SEAL_FILE)


def last_research_day() -> dt.date:
    return holdout_start() - dt.timedelta(days=1)


def research_files(
    dataset: str, start: dt.date = WINDOW_START, end: dt.date | None = None, root: Path = MASSIVE_ROOT
) -> list[tuple[dt.date, Path]]:
    """(date, path) of the raw files of ``dataset`` dated in [start, end], never past the day
    before the holdout."""
    hs = holdout_start()
    last = min(end or last_research_day(), hs - dt.timedelta(days=1))
    base = root / "raw" / DATASETS[dataset]
    out = []
    if not base.exists():
        return out
    for p in base.glob("*/*/*.csv.gz"):
        m = FILE_RE.search(p.name)
        if not m:
            continue
        d = dt.date.fromisoformat(m.group(1))
        if d >= hs:
            raise SealViolation(f"{p} is dated inside the holdout but sits under raw/; move it to sealed/")
        if start <= d <= last:
            out.append((d, p))
    return sorted(out)


@cache
def utc_offset_minutes(d: dt.date) -> int:
    """New York's offset from UTC at noon on ``d`` (-240 or -300). Clock changes happen at 02:00,
    never between 04:00 and 20:00, so one offset per date is exact for every bar we use."""
    return int(dt.datetime(d.year, d.month, d.day, 12, tzinfo=NY).utcoffset().total_seconds() // 60)


def hhmm(minute: int) -> str:
    return f"{minute // 60:02d}:{minute % 60:02d}"
