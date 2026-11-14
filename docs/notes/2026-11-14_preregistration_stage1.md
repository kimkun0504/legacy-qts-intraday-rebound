# Pre-registration — intraday rebound, stage 1 (daily bars), 2026-11-14

Binding from the commit that adds this file. Family `intraday_rebound_stage1:<first 12 hex of
this file's sha256>`. Disclosure: this follows nine failed trials in `qts-small-cap`
(family `smallcap_signals:adb23432d840`), whose one robust finding (news-free weekly reversal,
NW t 3.2 in ranks 1,001-3,000 and 5.2 in micro caps) motivates this test. That is a choice made
after seeing data; the seal (data from 2023-11-09) still protects any final check.

## What stage 1 is
A **gate**, not a strategy. It asks whether a deep intraday drop without news is bought back
by the close, using only daily open/low/close. Fills judged from a daily low are optimistic
(an odd-lot or out-of-hours print can fake a fill on a day the stock never really traded there,
and those fake trades look profitable). So a **pass only opens stage 2** (minute bars, regular
hours, then quotes); a fail ends the idea cheaply. Nothing is traded on a stage-1 result.

## Rule (fixed)
- **Universe on day D:** the weekly universe of the most recent formation Friday before D
  (`qts_smallcap.research.panel`): small caps = ranks 1,001-3,000 (as-traded close >= $5, 63-row
  mean dollar volume >= $1M); micro caps = ranks 3,001-6,000 (>= $2, >= $100k). Ranks over all
  names in the store, point in time.
- **Order before D's open:** buy limit at L = C(D-1) x (1 - 2 sigma), sigma = sd of daily
  close-to-close returns over the 60 rows ending D-1 (split-adjusted SEP prices throughout).
- **Fill:** if open <= L, filled at the open (the opening auction); else filled at L only if
  the day's low <= L x (1 - 0.005) (a 0.5 % trade-through, not a touch); no volume, no fill.
- **Exit:** at the close of D (MOC). Trade return r = close / fill - 1.
- **News known before the open:** an earnings announcement (`earnings_dates`) dated D or D-1,
  or any 8-K (`sharadar_events`) dated D-1. 8-Ks dated D are not used: they may be filed
  during the day, and excluding them would use information not available at the order.
- **Costs per trade, $500 position (a $10k sleeve, ~20 positions a day), whole shares at the
  as-traded price:** buy = IBKR Tiered fees for a passive limit order (no spread, no impact,
  no auction fee; adverse selection is in r itself); sell = MOC through
  `qts_smallcap.costs.model.SmallAccountCostModel` (fees + auction impact with the name's
  63-row ADV and 60-row volatility).
- **Window:** trading days 2003-06-02 .. 2023-11-08 (prices before the seal only).

## Trials (one each, counted)
| ID | Universe | Days |
|---|---|---|
| R1 | small caps | no news known before the open |
| R2 | micro caps | no news known before the open |
| R3 | small caps | news known before the open (the "news oversell rebound") |

## Pass (all three, per trial)
1. Newey-West t (lag 5) of the mean daily gross return g_t (equal-weighted over the day's
   fills) >= 3.0, and Holm-adjusted two-sided p < 0.05 over R1-R3.
2. Mean net return per trade (r minus the round trip) > 0.
3. Mean g_t > 0 in >= 4 of 6 equal time folds.

## Reported, not used for selection
Per-trade raw gain (mean, median, win rate: the owner's 4 % target), fills per day, by year,
fills at the open vs. intraday, minus IWM's open-to-close return, thresholds 1.5 and 3 sigma,
trade-through 0 % and 2 %, and exit at the next day's close. Any of these becomes a trial only
through a new addendum written before it is run.
