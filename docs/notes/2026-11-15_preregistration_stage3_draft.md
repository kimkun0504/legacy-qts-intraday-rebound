# DRAFT pre-registration, stage 3: the opening sweep on the holdout

**Draft. Not registered, nothing run.** It becomes binding only when the owner approves it, it is
committed unchanged, and its hash is recorded in the ledger. The holdout (2023-11-09 on) is opened
only with the owner's explicit go-ahead, once.

## Why
The exploratory note `2026-11-15_exploration_open_sweep.md` found a rule that earned about +3 % net
a trade in every period of the research window. It was chosen after looking, so the research
window cannot test it. The holdout can.

## Data
Massive minute and daily flat files, 2023-11-09 to the last complete day at registration; Sharadar
daily data for the universe, sigma, ADV and the news flag, as in stages 1 and 2. Same name
matching and the same two data gates as stage 2, computed on the holdout before any return.

## Trials (Holm over 3)
All: micro caps without news; pre-market dollar volume (04:00-09:24) under 1 % of the 63-day ADV;
buy limit placed before the open; unfilled orders cancelled at 09:35; exit MOC; $500 a position.

| Trial | Limit | Fill model |
|---|---|---|
| O1 | prior close x (1 - max(2 sigma, 5 %)) | stage 2: auction part capped at 25 % of the first minute; else 0.5 % trade-through and 2x volume |
| O2 | as O1 | strict: no auction part; 5 % trade-through and 5x volume |
| O3 | prior close x (1 - max(2 sigma, 8 %)) | as O1 |

## Statistics and pass rules
Trade-weighted net return (stage-2 cost model); NW(5) t on the daily P&L sum. A trial passes with
all of: t at least 3 with Holm p under 0.05; net at least +1.5 % a trade (half of what the research
window showed); at least 4 of 6 folds positive; net still positive with 1 % extra round-trip cost.

## Reported, not used for selection
By year and month, ADV and price buckets, auction and continuous fills, the 60-minute exit, the
number of resting orders the rule needs, and the tick check on a random sample of holdout fills.

## What follows
No pass: the idea stops. Any pass: IBKR paper trading for at least 40 days, then a live pilot at
the smallest size. No parameter is changed after the holdout is opened.

## Open before registering
- The tick check on the research-window sample (are the prints fills?).
- Whether IBKR accepts several hundred resting orders against the account.
- The owner's go-ahead.
