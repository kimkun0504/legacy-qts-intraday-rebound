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

## Pre-registered sensitivity (reported only; run 2026-11-15)

Net per trade and NW t, `data/stage2/results_sensitivity.json`:

| Fill variant | P1 | P2 | P3 |
|---|---|---|---|
| base (0.5 % trade-through, 2x volume, auction cap 25 %) | +0.74 % (16.1) | +0.15 % (1.4) | +0.79 % (17.0) |
| no trade-through needed | +0.97 % (24.5) | +0.54 % (5.7) | +1.01 % (25.3) |
| 1 % trade-through | +0.48 % (8.9) | -0.24 % (-2.1) | +0.54 % (10.0) |
| 2 % trade-through | **+0.00 % (0.0)** | -0.98 % (-6.7) | +0.08 % (1.1) |
| auction cap 10 % / 50 % | +0.73 % / +0.74 % | +0.15 % | +0.77 % / +0.79 % |
| no volume rule | +0.77 % (16.7) | +0.15 % (1.5) | +0.81 % (17.6) |
| auction part priced at the limit | +0.43 % (9.2) | -0.39 % (-3.6) | +0.62 % (13.6) |
| cut-off 15:30 | +0.79 % (16.4) | +0.18 % (1.6) | +0.84 % (17.4) |

The pass is fragile to queue position: if the price has to trade 2 % through the limit before the
order counts as filled, P1 earns nothing. The all-day rule lives on fills at shallow touches, where
a real order may sit behind others. (The opening-window rule in the exploratory note keeps +3.1 %
under a 5 % trade-through.)

## Not yet done

The diagnostics module and the quote check (`run_stage2.py quote-sample` is written; the owner runs
`scripts/massive.py quotes` on the Mac). They are reported only and select nothing.
