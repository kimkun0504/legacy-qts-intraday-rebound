"""Sibling checkouts under one folder (``~/Desktop/QTS``); override with environment variables."""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
QTS_REPO = Path(os.environ.get("QTS_REPO", REPO_ROOT.parent / "quant-trading-system"))
SMALLCAP_REPO = Path(os.environ.get("QTS_SMALLCAP_REPO", REPO_ROOT.parent / "qts-small-cap"))
SHARED_STORE = Path(os.environ.get("QTS_SHARED_STORE", QTS_REPO / "data" / "store"))
SMALLCAP_STORE = Path(os.environ.get("QTS_SC_STORE", SMALLCAP_REPO / "data" / "store"))
#: duckdb file with the weekly-panel intermediate tables (px2, cal, uni, uni_micro)
PANEL_DB = Path(os.environ.get("QTS_SC_PANEL_DB", Path.home() / "work" / "panel.duckdb"))
SEAL_FILE = REPO_ROOT / "research" / "seal.yaml"
LEDGER = REPO_ROOT / "runs" / "trial_ledger.jsonl"
OUT = REPO_ROOT / "data" / "stage1"
