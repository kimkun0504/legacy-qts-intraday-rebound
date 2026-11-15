# Exploration: entries, exits and volume on the minute bars (2026-11-15)

**Status: exploratory, not a trial.** The owner asked for a search over entry and exit rules on
the minute bars, with volume as a suspect, aiming at 3-4 % a trade. Everything here was chosen
after looking at the research window (2003-09-10 to 2023-11-08), so none of it is evidence yet.
The holdout (from 2023-11-09) is still sealed and is the only clean test left. About 2,300
variants were tabulated (`data/explore/scan.parquet`); the look is logged in the ledger.

**Bottom line.** One rule reaches +3 to +4 % a trade in the bar-level backtest. An independent
review of the code then showed that about half of that comes from two generous assumptions
(price improvement at the open, and selling at the last print). With both removed the rule earns
about **+2 %** a trade (about +3.5 % for sweeps of 8 % or more filled after the open); under the
harshest reading it earns little or nothing. It needs resting orders in
hundreds of names, so the return on capital depends on the broker, not on the return per trade.

Code: `qts_rebound/explore/` and `scripts/explore.py` (`build`, `scan`, `tick-sample`,
`tick-check`, `export`), `scripts/explore_universe.py`. Universe: micro caps without news, as in
stage 2. Costs: the stage-2 cost model (median 0.36 % round trip here). Position $500.

## What does not work

Every entry x exit pair without further conditions:

- **Exits.** Selling at the close (MOC) is as good as anything. Profit targets and stop-losses
  lower the average at every level tried; 60- and 120-minute time exits are close to MOC.
- **Deeper limits** (3 to 8 sigma) earned 1.7-4.0 % net in 2003-2016 but 0.7-1.3 % in 2017-2023.
- **Waiting for confirmation** (a reversal bar, a volume spike, a fixed clock time) is worse than
  the resting limit in both periods. Entries after 10:00 earn about nothing after costs.
- Best unconditioned pair in 2017-2023 with at least 300 trades: +1.7 % net (t 2.2).

## What stands out: the opening sweep

The stage-2 gain is concentrated in three overlapping conditions, each known before or at the fill:

1. **The fill comes in the first minutes.** Orders filled by 09:35 earn several times the later ones.
2. **There was almost no pre-market trading.** Pre-market dollar volume (04:00-09:24) under 1 % of
   the 63-day ADV. With pre-market trading the same fills earn about nothing.
3. **The drop is real in percent, not only in sigmas.** A floor of 5 % below the prior close removes
   the low-volatility names (SPACs in 2020-2022) whose 2-sigma limit is under 1 % away.

On days with news the same rule earns +0.3 % (2003-2016) and -0.6 % (2017-2023): drops with a
reason do not come back.

**Rule "open sweep 5".** Before the open, a buy limit at `prior close x (1 - max(2 sigma, 5 %))`
in every no-news micro cap whose pre-market dollar volume is under 1 % of ADV. Cancelled at 09:35.
Sold at the close.

| Net per trade | 2003-2016 | 2017-2023 | 2020-2023 |
|---|---|---|---|
| Trades (a day) | 8,301 (2.5) | 2,690 (1.6) | 1,654 (1.7) |
| **As modelled** (stage-2 fills, exit at the official close) | **+4.23 %** (t 19.2) | **+3.36 %** (t 10.6) | **+2.97 %** (t 6.2) |
| Auction shares priced at the limit, not at the open | +2.99 % | +2.45 % | +1.92 % |
| **Conservative**: that, and sold at the low of the bar 60 minutes after the fill | **+2.15 %** (t 19.9) | **+2.34 %** (t 10.3) | **+1.94 %** (t 5.7) |
| Harshest exit: the lowest low of the last ten bars of the day | +1.36 % | +1.18 % (t 2.3) | +0.57 % (t 0.7) |
| Fill only if the price trades 2 % through the limit by 09:35, 5x volume, no auction part | +2.17 % | +1.72 % (t 3.2) | +0.99 % |
| The same with 5 % through | +0.76 % | 0.00 % | -1.33 % |

As modelled: median +3.9 %, 77 % wins, worst 5 % of trades -8 % (2017-2023). Net by year, as
modelled / conservative (%): 2003 +5.7/+2.7, 2004 +5.0/+2.2, 2005 +4.0/+1.6, 2006 +3.8/+1.9,
2007 +3.3/+1.6, 2008 +4.7/+2.1, 2009 +4.7/+2.7, 2010 +4.8/+2.4, 2011 +3.4/+2.0, 2012 +4.3/+1.8,
2013 +3.9/+2.6, 2014 +3.6/+2.7, 2015 +4.0/+2.8, 2016 +3.9/+2.9, 2017 +4.2/+2.9, 2018 +3.9/+3.2,
2019 +3.9/+2.7, **2020 +0.3/0.0**, 2021 +4.1/+2.8, 2022 +4.4/+2.9, 2023 +2.6/+1.9.

Other variants as modelled (2003-2016 / 2017-2023): floor 8 % +5.52 / +4.29 %; position $2,000
+4.09 / +3.19 %; only ADV of $1M or more +2.66 / +2.53 %. Sensitivity is smooth (2017-2023):
cancel at 09:31 +3.7 %, 09:35 +3.4 %, 09:40 +3.2 %, 10:00 +2.6 %, never +0.7 %; pre-market cap
0.1 % of ADV +3.7 %, 1 % +3.4 %, 5 % +3.1 %, none +2.1 %.

## The variant that reaches 3-4 % under the conservative reading

Deeper sweeps earn more, and fills at the open are the weak part. Both together, still with
every share priced at the limit and sold at the low of the bar 60 minutes after the fill:

| Conservative net per trade | 2003-2016 | 2017-2023 | 2020-2023 |
|---|---|---|---|
| Floor 8 % (4,294 / 1,512 / 956 trades) | +2.96 % (t 15.2) | +3.27 % (t 11.5) | +2.82 % (t 7.3) |
| Floor 8 %, only fills after the open (2,665 / 1,092 / 656) | **+3.57 %** (t 15.5) | **+3.93 %** (t 13.7) | **+3.50 %** (t 9.0) |
| The same in names with ADV of $300k or more (1,358 / 691 / 433) | +3.28 % | +3.66 % | +3.13 % |
| Floor 8 %, harshest exit, all fills | +2.15 % | +1.84 % (t 2.8) | +1.05 % (t 1.0) |

"Only fills after the open" means the order takes no part in the opening auction: in practice it
is sent just after 09:30, and days when the stock opens below the limit are skipped. That is
about 155 trades a year (median +4.9 %, 80 % wins in 2017-2023). It is one more choice made after
looking, so it carries the same caveats as everything here, and the harshest exit still halves it.

## What the review changed (2026-11-15)

A second pass over the code by an independent reviewer found no look-ahead (sigma, ADV, universe,
news flag, pre-market window and the past-sweeps feature are all known before the open) but four
things that flattered the first numbers. All are now measured above:

1. **Price improvement at the open.** If the first bar opens below the limit, the model fills at
   that open, as if our own bid would not have moved a 100-200 share cross. Pricing those shares
   at the limit takes about 0.9 points off. Fills at the open are the weak part: priced at the
   limit and with the harshest exit they lose money in 2017-2023, while fills during the first
   minutes still earn +2.0 %.
2. **The "strict fill" check was not strict.** It left the auction part untouched and used a
   filter instead of a real cancel time. Done properly (table above), the edge falls with every
   step and is gone at 5 %. So the profit is in shallow sweeps; when the price runs far through
   the limit the trade loses. An earlier version of this note claimed the opposite.
3. **The exit.** The official close can be an hours-old or ask-side print in names with 5-10 %
   spreads. Selling at the low of a bar an hour later costs about 1 point; the harshest reading
   (worst print among the last ten bars) costs 2.
4. **Small things, small effect:** the 09:35 cancel is now a real cancel (was a filter); 1 % of
   fills are under $250; 6 % of fills are in names with no trade in the last half hour.

Still open from the review: day-D special dividends and spin-offs are not excluded (they add
noise, not profit); the exported order sheet takes the raw prior close, so its limits are wrong
on split days; the 0.3 % fill rate counts news-day orders in the denominator.

A cross-check on the prints: Sharadar's daily low, from a different vendor, is at or below the
limit on 98 % (2003-2016) and 99.8 % (2017-2023) of the fill days.

## Volume

The owner's hunch holds, with the sign reversed from "capitulation volume": the gain is in drops
on **no** volume. No pre-market trading, a thin first minute and little volume before the fill all
raise the return; entries after a volume spike do not. These are sell orders hitting an empty book
at the open, and the price is back within minutes.

## Other universes, rule unchanged

The rule was shaped on micro caps only (`scripts/explore_universe.py`):

| Net, as modelled | 2003-2016 | 2017-2023 | 2020-2023 |
|---|---|---|---|
| Small caps (12,810 / 7,396 / 5,098 trades) | +2.40 % (t 6.3) | -0.02 % | -1.15 % |
| Small caps, sold at the low of the bar 60 minutes on | +2.26 % | +0.83 % (t 1.9) | +0.19 % |
| Large caps (5,163 / 3,485 / 3,005 trades) | +1.91 % (t 2.8) | -1.39 % | -1.80 % |

Small caps are positive in 20 of 21 years (+1.3 % to +4.3 % in most) and **-3.2 % in 2020**: on
the crash days of March 2020 liquid names opened 5 % down and kept falling, and those days carry
many fills. Large caps are mostly between -0.4 % and +2.4 % a year. By liquidity in small caps
(2017-2023): ADV $1-3M +1.5 %, $3-10M +0.2 %, above $10M -1.1 %.

Reading: the direction carries over to stocks that were never looked at and shrinks with
liquidity, which fits a payment for supplying liquidity at the open. It also shows the tail: when
the whole market gaps down, every resting order fills and loses together. A market-wide stop (no
orders when index futures are down hard before the open) is the obvious guard; it is untested, to
avoid shaping the rule further.

## What it earns on capital: the order budget

A few percent a trade is not the return on the account. Only 0.3 % of resting orders fill, so the
scarce thing is how many orders can rest. A model of fill probability (sigma, ADV, price, past
sweeps in the name; fitted on 2003-2012, applied to 2013-2023) picks which names get an order:

| Orders resting each day | Fills a year | Net a trade (as modelled) | Profit a year at $500 an order |
|---|---|---|---|
| 20 | 35 | +3.7 % | $640 |
| 50 | 74 | +3.5 % | $1,300 |
| 100 | 132 | +3.6 % | $2,400 |
| 200 | 229 | +3.6 % | $4,100 |
| 400 | 346 | +3.5 % | $6,000 |
| all (676) | 400 | +3.6 % | $7,100 |

Halve the profit column for the conservative estimate. Ranking helps a little and the return per
fill does not depend on the rank, so profit is roughly `0.5 % x 2-3.6 % x 252 = 2.5-4.5 % a year`
of the money tied up in resting orders. If every open order must be fully funded, $10,000 earns
$250-$500 a year from this rule. If the broker checks buying power only at the fill, or allows 4x
intraday margin, the same $10,000 can carry far more orders. That is the first thing to find out
in the paper account. (The $25,000 pattern-day-trader minimum no longer applies: FINRA replaced it
with intraday margin rules from 2026-06-04.)

## Why this may not be real money (in order of weight)

1. **The orders have to be resting, in hundreds of names.** Reacting after the print does not
   work: a limit placed after the first trade at the limit earns +0.2 % / 0.0 %, a market order at
   the next minute's open +1.9 % / +1.5 %.
2. **Are the prints fills, and at what price can one sell?** Many fills are one or two trades of
   100-200 shares. Retail sell orders are often filled off-exchange and would not reach a resting
   bid. `scripts/explore.py tick-sample` wrote 400 fills; `scripts/massive.py ticks` (Mac) fetches
   the trades and quotes around them; `scripts/explore.py tick-check` reports the share on lit
   exchanges and the spread.
3. **Selection.** The rule was shaped on the whole research window. The t-statistics are far
   beyond what a few thousand looks produce by chance, but only the holdout can confirm it.
4. **2020.** In the March 2020 crash the drops kept going.

## Proposed next steps

1. Owner, on the Mac: `python3 scripts/massive.py ticks`, then `python scripts/explore.py tick-check`.
2. Paper account: place a few hundred small resting limit orders before the open and see whether
   IBKR accepts them (buying power).
3. If both look right: finish the stage-3 pre-registration
   (`2026-11-15_preregistration_stage3_draft.md`) and run it once on the holdout, only with the
   owner's go-ahead.
4. Then a live pilot at the smallest size; one or two fills a day make it cheap to learn from.
