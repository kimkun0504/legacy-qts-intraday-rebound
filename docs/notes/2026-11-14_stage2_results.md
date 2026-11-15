# Stage 2 results: minute-bar fills (2026-11-14)

Family `intraday_rebound_stage2:ed85b682c42e`, pre-registered in
`2026-11-14_preregistration_stage2.md` before any Massive data was analysed. Code `dff7278`.
Research window 2003-09-10 to 2023-11-08; the holdout (from 2023-11-09) stayed sealed.

## Data gates (both passed)

| Gate | Result | Needed |
|---|---|---|
| Name matching, P1 set | 96.8 % overall, worst year 92.5 % | 95 % and 90 % |
| Name matching, P2 set | 97.3 % overall, worst year 91.3 % | 95 % and 90 % |
| Matched stock-days with regular-hours minute bars | 99.94 % (370,057 of 370,293) | 95 % |

## Trials (Holm over 3)

| Trial | Universe | Trades | Gross | Net | NW t | Folds positive | Capped book net | Verdict |
|---|---|---|---|---|---|---|---|---|
| P1 | micro caps, no news | 123,149 (24.8 a day) | +1.08 % (median +0.77 %, 61 % wins) | **+0.74 %** | 16.1 | 6 of 6 | +1.15 % | **pass** |
| P2 | micro caps, ADV at least $1M | 21,959 (5.8 a day) | +0.44 % | +0.15 % | 1.4 | 3 of 6 | +0.06 % | **fail** |
| P3 | P1 minus pre-market dollar volume of 5 % of ADV or more | 118,994 (23.9 a day) | +1.12 % | **+0.79 %** | 17.0 | 6 of 6 | +1.23 % | **pass** |

## Reading

- The rebound survives minute-bar fills with a trade-through rule: it was not only an artefact
  of daily prints.
- It sits in the thin names: P2 fails three of the four rules.
- It has faded: P1's net per trade was -0.07 % in 2020, +0.21 % in 2022 and -0.19 % in 2023,
  against +0.6 % to +0.9 % in most of 2012-2019.
- Opening-auction fills (7 % of trades) earn +2.5 % net; fills during the day earn +0.59 %.
  The exploratory note `2026-11-15_exploration_open_sweep.md` follows that lead.

## Not yet done

The pre-registered sensitivity variants (`run_stage2.py fills --variants ...`), the diagnostics
module, and the quote check (`run_stage2.py quote-sample` is written; the owner runs
`scripts/massive.py quotes` on the Mac). They are reported only and select nothing.
