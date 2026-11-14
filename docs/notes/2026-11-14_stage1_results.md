# 2026-11-14 — Stage 1 results (daily bars, 2003-06 .. 2023-11, before the seal)

Family `intraday_rebound_stage1:59a5ecfc6305`; ledger `runs/trial_ledger.jsonl` (local).
Rule: buy limit 2 sigma below the prior close, filled at the open on a gap through, else on a
0.5 % trade-through of the daily low; sold at the close; $500 positions, IBKR Tiered fees,
MOC impact on the sell.

| Trial | Universe, days | Trades (per day) | Gross per day, NW t | Net per trade | Folds + | Raw gain per trade (median, win) | Verdict |
|---|---|---|---|---|---|---|---|
| R1 | small caps, no news | 280,104 (54.5) | +0.55 %, t 20.8 | **-0.16 %** | 6/6 | +0.02 % (-0.19 %, 45 %) | fail (net) |
| R2 | micro caps, no news | 146,355 (29.0) | +1.72 %, t 20.1 | **+0.93 %** | 6/6 | +1.26 % (+0.67 %, 60 %) | **pass the gate** |
| R3 | small caps, news known before the open | 74,545 (15.1) | -0.21 %, t -4.8 | -0.59 % | 0/6 | -0.40 % (-0.29 %, 46 %) | fail |

Holm over R1-R3: all p < 1e-4 (R3 significant in the wrong direction).

## Reading
- **R3 answers the "news oversell rebound" question: no.** Deep drops on days with news known
  before the open keep falling into the close, in every fold.
- **R1:** the daily average is positive only because quiet days with few fills earn more than
  crash days with many; trade-weighted, small-cap fills earn nothing and lose the fees.
- **R2 passes, and must be treated as a likely overstatement until stage 2.** Checks (reported
  only):
  | Subset of R2 | Trades | Mean | Median |
  |---|---|---|---|
  | all | 146,355 | +1.26 % | +0.67 % |
  | fills at the open (gap through the limit) | 20,492 | +3.52 % | +1.60 % |
  | opens more than 15 % below the prior close | 1,734 | **+13.3 %** | +3.47 % |
  | intraday fills only | 125,863 | +0.89 % | +0.55 % |
  | low more than 10 % below the limit (spike) | 7,850 | -2.42 % | -5.27 % |
  | 63-day ADV >= $1M | 24,587 | +0.53 % | +0.20 % |
  | 63-day ADV < $300k | 68,592 | +1.73 % | +1.02 % |
  | trade-through 2 % / 5 % instead of 0.5 % | 82,198 / 41,055 | +0.79 % / +0.59 % | +0.20 % / +0.24 % |
  | exit at the next close | 146,288 | +1.29 % | +0.95 % |
  Excluding the 2010-05-06 flash crash changes nothing. The top 1 % of trades carry 26 % of
  the gross P&L.
- **Red flags:** the gain concentrates where daily prints are least reliable: opening prints
  far below the prior close (+13 % a trade is not credible as a fill a $500 order would get)
  and the thinnest names. Deep spikes lose money, so fake lows are not the main driver, but
  bad opens may be. The most liquid micro caps (ADV >= $1M) still earn +0.24 % net a trade.

## Next (stage 2)
Minute bars, regular hours only, for every micro-cap stock-day whose daily low reached the
limit (about 150-200k stock-days): verify that the low and the open were real regular-hours
prints with enough volume for a $500 order, measure the rebound from the actual fill time,
and test post-open entry rules under a new pre-registration.
