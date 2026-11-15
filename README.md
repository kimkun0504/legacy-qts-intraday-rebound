# qts-intraday-rebound

Does a deep intraday drop **without news** get bought back by the close? Built on
[quant-trading-system](https://github.com/kimkun0504/quant-trading-system) (shared Sharadar
store, read-only) and [qts-small-cap](https://github.com/kimkun0504/qts-small-cap) (its
OHLC/fund tables, weekly point-in-time universe and small-account cost model).

Staged so money is spent only when a stage earns it:
1. **Stage 1 (daily bars, free):** a buy limit placed before the open at 2 sigma below the
   prior close, filled only on a trade-through, sold at the close. A gate, because fills from
   daily lows are optimistic. Rules: `docs/notes/2026-11-14_preregistration_stage1.md`;
   results: `docs/notes/2026-11-14_stage1_results.md` (micro caps pass the gate, likely
   overstated).
2. **Stage 2 (Massive minute bars, 2003-09 on):** the same orders with fills, timing and
   volume judged from regular-hours minute bars, a liquid variant and a pre-open news proxy.
   Rules: `docs/notes/2026-11-14_preregistration_stage2.md`. Data download (on the Mac):
   `docs/notes/2026-11-14_stage2_data_runbook.md` and `scripts/massive.py`.
3. Stage 3: the holdout (with the owner's go-ahead), then IBKR paper trading.

Expected layout: `~/Desktop/QTS/{quant-trading-system, qts-small-cap, qts-intraday-rebound}`.
The 2023-11-09 seal of the other projects applies here too (`research/seal.yaml`); Massive
files dated on or after it are kept under `data/massive/sealed/`, unread.

Secrets: Massive keys live only in `.env` (gitignored; template `.env.example`).
