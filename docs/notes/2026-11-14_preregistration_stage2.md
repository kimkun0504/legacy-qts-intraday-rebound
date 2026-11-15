# Pre-registration: intraday rebound, stage 2 (minute bars), 2026-11-14

Binding from the commit that adds this file. Family `intraday_rebound_stage2:<first 12 hex of
this file's sha256>`. The evaluation code is committed before its first run, and every ledger
record carries that commit.

**Disclosures.** Written after stage 1 (`docs/notes/2026-11-14_stage1_results.md`): R2
(micro caps, no news) passed the gate with red flags, R1 and R3 failed, and an exploratory look
found no rebound in ranks 1-1,000. P2's liquidity cut (63-day ADV >= $1M) comes from a stage-1
diagnostic (+0.53 % gross, +0.24 % net a trade): a choice made after seeing data. Counted
trials so far: 9 (`smallcap_signals:adb23432d840`) + 3 (`intraday_rebound_stage1:59a5ecfc6305`);
this file adds 3. The seal still protects the final check. **Data status at this commit:** no
Massive data has been analysed. The downloader's `check` step reads only a header, a row count
and a ticker list from the first day's files (2003-09-10).

## What stage 2 asks
Does stage 1's R2 survive realistic fills? The same orders, with each fill's timing, price and
volume judged from **regular-hours minute bars built only from eligible trades** (Massive
excludes odd lots and other non-price-setting prints from bar prices), so a stray daily low or
opening print can no longer make a fill.

## Data
- **Massive flat files** (SIP, unadjusted): minute bars `us_stocks_sip/minute_aggs_v1` and
  daily bars `day_aggs_v1`. Bar time = `window_start` converted to New York time per date.
  Regular hours = bars starting 09:30 to 15:59 (to 12:59 on early-close days), pre-market =
  bars starting 04:00 to 09:24.
- **Sharadar**, exactly as in stage 1: universe, sigma, 63-day ADV, news flags, and the exit
  price (the official close, as traded: `closeunadj`).
- **Window:** trading days 2003-09-10 (Massive's first minute file) to 2023-11-08 (the last
  day before the seal). Files dated 2023-11-09 or later sit in `data/massive/sealed/` and are
  not read.
- **Early-close days:** days with no IWM regular-hours bar starting at or after 13:00.
- **Which stock-days:** only stage 1's candidates (Sharadar's daily low at least 1.5 sigma below
  the prior close). A fill that Massive shows but Sharadar's low misses by more than about
  0.5 sigma is not counted (expected to be rare; the reconciliation below measures the gap).

## Matching Sharadar names to Massive symbols
Massive keys bars by the symbol traded that day; Sharadar files history under the current
ticker and suffixes reused ones (`ABC1`). For each candidate stock-day (Sharadar ticker T,
day D):
1. **Candidate symbols:** T and every `relatedtickers` entry, each with trailing digits
   removed, each also with its class separator ("." in Sharadar) written as ".", "-", "/" or
   nothing.
2. **Accept** a candidate symbol whose Massive daily bar on D closes within 0.5 % of
   Sharadar's as-traded close and has volume within a factor of 2 of Sharadar's as-traded
   volume (volume x close / closeunadj). If several pass, take the closest close.
3. **Otherwise**, among all Massive symbols on D, accept the unique one with close within 0.1 %
   and volume within a factor of 1.25. No unique match: the stock-day is unmatched.
Unmatched stock-days are dropped and counted; their stage-1 daily-bar returns are reported.

## Data-quality gates (checked before any trade return is computed)
1. Matched share of the stock-days in P1's and P2's candidate sets is >= 95 % over the window
   and >= 90 % in every calendar year.
2. Of matched stock-days, >= 95 % have at least one regular-hours minute bar.

If a gate fails, nothing is evaluated: the shortfall is reported and fixed only through an
addendum written before any evaluation.

## Orders (unchanged from stage 1 except where marked)
- **Universe, news flag, sigma, ADV:** stage 1's tables (`data/stage1/candidates_*.parquet`),
  point in time, unchanged.
- **Limit:** L = C(D-1) x (1 - 2 sigma) in split-adjusted terms, as traded on D:
  L_D = L x closeunadj(D) / close(D).
- **Size (changed):** q = floor($500 / L_D) whole shares, at least 1 (stage 1 rounded).
- **Lifetime (new):** placed before the open; cancelled if unfilled by the cut-off: 15:45, or
  12:45 on early-close days (15 minutes before the close, so the exit can be sent as MOC).

## Fill model (new)
Bars b = 1, 2, ... are the regular-hours minute bars of D starting before the cut-off, with
open O_b, low Lo_b, volume V_b. The trade-through is 0.5 %, as in stage 1.
- **Continuous fill:** all q shares fill at L_D at the end of the first bar b by which both
  (i) some bar has traded at or below L_D x 0.995 and (ii) at least 2q shares have traded in
  bars whose low is at or below L_D.
- **Opening-auction fill:** if bar 1 starts at 09:30 and O_1 <= L_D, the
  order joins the opening auction: q_a = min(q, floor(0.25 x V_1)) shares fill at O_1. The
  rest, q - q_a, fills at L_D under the continuous test with q - q_a in place of q, if it
  passes; otherwise only q_a shares are held (no trade if q_a = 0).
- **Fill price:** the share-weighted price of the parts. **Entry time:** the first execution
  (09:30 for an auction part, else the end of the fill bar).
- **Exit:** MOC at the official close of D (Sharadar `closeunadj`). Gross return
  r = closeunadj(D) / fill price - 1.

## Costs (as stage 1, $500 positions)
Buy: IBKR Tiered fees for one order of the filled shares (`qts.tcost.fees.FeeSchedule`), plus
the auction exchange fee on the auction part; no spread, no impact (adverse selection is in
r). Sell: MOC through `qts_smallcap.costs.model.SmallAccountCostModel` (fees + auction impact
with the name's 63-day ADV and 60-day sigma). Net = r - (buy + sell) / (shares x fill price).

## Trials (one each, counted; Holm over the three)
| ID | Universe | Days |
|---|---|---|
| P1 | micro caps (stage-1 R2 universe) | no news known before the open (stage-1 flag) |
| P2 | micro caps with 63-day ADV (as of D-1) >= $1M | no news known before the open |
| P3 | as P1 | ... and pre-market dollar volume < 5 % of the 63-day ADV |

Pre-market dollar volume: the sum of volume x close over the bars starting 04:00-09:24.

P3's pre-market test is knowable at 09:25, when the order can still be cancelled.

## Statistics
- Per trade: gross r and net. Per day: S_d = sum of the day's net returns (units of one $500
  position), 0 on trading days without fills, over every trading day of the window.
- **Main number:** mean net per trade (trade-weighted: sum of net / trades).
- **t-statistic:** Newey-West (Bartlett, 5 lags) t of the mean of S_d; two-sided p from the
  normal distribution; Holm over P1-P3. (Changed from stage 1, whose daily mean of gross
  returns gave quiet days with few fills the weight of crash days with many.)
- **Folds:** the window's trading days in 6 equal consecutive blocks; a fold is positive if its
  S_d sum to more than 0.
- **Capped book:** at most 20 fills a day per trial (a $10k sleeve of $500 positions), taken in
  order of entry time; ties go to the higher 63-day ADV, then the ticker. Later fills of that
  day are treated as cancelled.

## Pass (all four, per trial)
1. NW t >= 3.0 and Holm-adjusted p < 0.05.
2. Mean net per trade >= +0.30 % (room for live slippage).
3. >= 4 of 6 folds positive.
4. Capped book: mean net per trade > 0.

## Reported, not used for selection
Any of these becomes a trial only through a new pre-registration written before it is run.
- The same stock-days with stage 1's daily-bar fills (what minute data changes).
- Trade-through 0 %, 1 %, 2 %; auction participation 10 % and 50 %; no volume test; the auction
  part priced at L_D instead of O_1; a 15:30 cut-off.
- By fill type (auction or continuous), entry half-hour, year, ADV bucket, price bucket.
- The path after the fill: mean return from entry to each hour mark and to the close; the
  worst and best price reached after entry.
- IWM-hedged return (short IWM at the entry time, cover at the close, IWM costs included).
- Days with more than 50 uncapped fills (market-wide sell-offs), and their share of P&L.
- Deep spikes: fills whose regular-hours low is more than 10 % below L_D.
- Reconciliation of Massive's regular-hours open, low and close with Sharadar's; share of
  stage-1 fills whose daily low no eligible regular-hours trade reached.
- **Quote check** (Massive quotes API, during the subscription): 500 P1 fills drawn at random
  (seed 20261114). Quoted spread at entry and at 15:55; whether the best bid stood at or above
  L_D in the minute of the fill.
- Deflated Sharpe ratio of the best trial, counting 15 trials.

## After stage 2
- **No trial passes:** the rebound idea stops. Diagnostics cannot rescue it; a new idea needs a
  new pre-registration and counts as a new trial.
- **One or more pass:** with the owner's explicit go-ahead, each passing trial is run once,
  unchanged, on the holdout (2023-11-09 to the last Massive date). Holdout pass: mean net per
  trade > 0, NW t >= 2.0, and the capped book's mean net > 0. Then stage 3: IBKR paper trading
  for at least 40 trading days, with real fills compared with the model (fill rate within
  30 % of the model's, mean entry price no more than 0.2 % worse than the model's).
