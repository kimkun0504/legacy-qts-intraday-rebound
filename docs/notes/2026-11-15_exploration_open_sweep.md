# Exploration: entries, exits and volume on the minute bars (2026-11-15)

**Status: exploratory, not a trial.** The owner asked for a search over entry and exit rules on
the minute bars, with volume as a suspect, aiming at 3-4 % a trade. Everything here was chosen
after looking at the research window (2003-09-10 to 2023-11-08), so none of it is evidence yet.
The holdout (from 2023-11-09) is still sealed and is the only clean test left. 1,312 variants
were tabulated (`data/explore/scan.parquet`); the look is logged in the ledger.

Code: `qts_rebound/explore/` and `scripts/explore.py` (`build`, `scan`, `tick-sample`,
`tick-check`). Universe: micro caps without news, as in stage 2. Costs: the stage-2 cost model
(median 0.36 % round trip here). Position $500.

## What does not work

Every entry x exit pair without further conditions (640 pairs):

- **Exits.** Selling at the close (MOC) is as good as anything. Profit targets and stop-losses
  lower the average at every level tried; 60- and 120-minute time exits are close to MOC.
- **Deeper limits** (3 to 8 sigma) earned 1.7-4.0 % net in 2003-2016 but 0.7-1.3 % in 2017-2023.
- **Waiting for confirmation** (a reversal bar, a volume spike, a fixed clock time) is worse than
  the resting limit in both periods. Entries after 10:00 earn about nothing after costs
  (+0.21 % in 2017-2023).
- Best unconditioned pair in 2017-2023 with at least 300 trades: +1.7 % net (t 2.2).

## What stands out: the opening sweep

The stage-2 gain is concentrated in three overlapping conditions. Each is known before or at
the fill:

1. **The fill comes in the first minutes.** Limit orders filled by 09:35 earn several times the
   later ones.
2. **There was almost no pre-market trading.** Pre-market dollar volume (04:00-09:24) under 1 % of
   the 63-day ADV. With pre-market trading the same fills earn about nothing (-0.2 % to +0.2 %).
3. **The drop is real in percent, not only in sigmas.** A floor of 5 % below the prior close
   removes the low-volatility names (SPACs in 2020-2022) whose 2-sigma limit is under 1 % away.

On days with news the same rule earns +0.3 % (2003-2016) and -0.6 % (2017-2023): drops with a
reason do not come back.

**Rule "open sweep 5".** Before the open, a buy limit at `prior close x (1 - max(2 sigma, 5 %))`
in every no-news micro cap whose pre-market dollar volume is under 1 % of ADV. Cancel what has not
filled by 09:35. Sell at the close. Fills as in stage 2 (auction part capped at 25 % of the first
minute's volume; otherwise a 0.5 % trade-through plus twice the order's volume).

| | 2003-2016 | 2017-2023 | 2020-2023 |
|---|---|---|---|
| Trades (a day) | 8,266 (2.5) | 2,679 (1.6) | 1,644 (1.7) |
| Gross | +4.62 % | +3.75 % | +3.36 % |
| **Net** | **+4.24 %** | **+3.39 %** | **+3.01 %** |
| Median net | +4.33 % | +3.94 % | +3.64 % |
| Wins | 84 % | 77 % | 73 % |
| Worst 5 % of trades | -5.0 % | -7.9 % | -9.0 % |
| Net with 1 % extra cost | +3.24 % | +2.39 % | +2.01 % |
| NW t (daily sum) | 19.2 | 11.0 | 6.5 |

Net by year: 2003 +5.7, 2004 +5.0, 2005 +4.0, 2006 +3.8, 2007 +3.3, 2008 +4.8, 2009 +4.7,
2010 +4.8, 2011 +3.4, 2012 +4.3, 2013 +3.9, 2014 +3.6, 2015 +4.0, 2016 +3.9, 2017 +4.2,
2018 +3.9, 2019 +3.9, **2020 +0.5**, 2021 +4.1, 2022 +4.4, 2023 +2.6 (%). 98 % of months are
positive in both periods; the largest drawdown in 2017-2023 is 7 positions' worth.

Variants (net, 2003-2016 / 2017-2023 / 2020-2023):

| Variant | Net | Trades 2017-2023 |
|---|---|---|
| Floor 8 % instead of 5 % | +5.53 / +4.32 / +3.75 % | 1,506 |
| Floor 12 % | +7.48 / +6.07 / +4.93 % | 513 |
| Strict fill: price must trade 5 % through the limit, 5x the volume | +5.02 / +3.14 / +2.66 % | 1,086 |
| Sell 60 minutes after the fill instead of MOC | +3.52 / +3.41 / +3.18 % | 2,679 |
| Only names with ADV of $1M or more | +2.66 / +2.53 % | 433 |
| Position $2,000 / $5,000 (MOC) | +4.09 / +3.19 % and +3.90 / +3.06 % | 2,398 / 2,026 |

Sensitivity is smooth, not a knife edge (net, 2017-2023): cancel at 09:31 +3.73 %, 09:35 +3.39 %,
09:40 +3.22 %, 10:00 +2.55 %, never +0.66 %; pre-market cap 0.1 % of ADV +3.65 %, 1 % +3.39 %,
5 % +3.10 %, none +2.09 %.

## Volume

The owner's hunch holds, with the sign reversed from "capitulation volume": the gain is in drops
on **no** volume. No pre-market trading, a thin first minute and little volume before the fill all
raise the return; entries after a volume spike do not. These are sell orders hitting an empty book
at the open, and the price is back within minutes (median best price after the fill: +7 %).

## Out-of-universe check: small caps

The rule was shaped on micro caps only. The same rule, unchanged, in the small-cap universe
(`scripts/explore_universe.py small`; 290k stock-days reached the limit, 99 % matched):

| | 2003-2016 | 2017-2023 | 2020-2023 |
|---|---|---|---|
| Trades | 12,804 | 7,393 | 5,095 |
| Net | +2.40 % (t 6.3) | -0.02 % (t 0.0) | -1.14 % |
| Median net | +2.70 % | +0.99 % | -0.46 % |

By year it is positive in 20 of 21 years (+1.3 % to +4.3 % in most), and **-3.2 % in 2020**: on the
crash days of March 2020 liquid names opened 5 % down and kept falling, and those days carry many
fills. By liquidity (2017-2023): ADV $1-3M +1.5 %, $3-10M +0.2 %, above $10M -1.1 %.

**Large caps**, same rule (`scripts/explore_universe.py large`): +1.9 % in 2003-2016 (t 2.8),
-1.4 % in 2017-2023; by year mostly between -0.4 % and +2.4 %, -2.3 % in 2020. Little is left
where the book is deep.

Reading: the direction carries over to stocks that were never looked at, at about half the size,
and it shrinks with liquidity, which fits a payment for supplying liquidity at the open. It also
shows the tail: when the whole market gaps down, every resting order fills and loses together.
The micro-cap rule had the same year as its worst (+0.5 %). A market-wide stop (no orders when the
index futures are down hard before the open) is the obvious guard; it has not been tested, to avoid
shaping the rule further.

## What it earns on capital: the order budget

3-4 % a trade is not the return on the account. Only 0.3 % of resting orders fill, so the scarce
thing is how many orders can rest. A model of fill probability (sigma, ADV, price, past sweeps in
the name; fitted on 2003-2012, applied to 2013-2023) picks which names get an order:

| Orders resting each day | Fills a year | Net a trade | Profit a year at $500 an order |
|---|---|---|---|
| 20 | 35 | +3.7 % | $640 |
| 50 | 74 | +3.5 % | $1,300 |
| 100 | 132 | +3.6 % | $2,400 |
| 200 | 229 | +3.6 % | $4,100 |
| 400 | 346 | +3.5 % | $6,000 |
| all (676) | 400 | +3.6 % | $7,100 |

Ranking helps a little (the top 20 fill at 0.7 % against 0.3 %), and the return per fill does not
depend on the rank. So profit is roughly `0.5 % x 3.6 % x 252 = 4.5 % a year` of the money tied up
in resting orders. If every open order must be fully funded (a cash account), $10,000 earns about
$500 a year from this rule. If the broker checks buying power only at the fill, or allows 4x
intraday margin, the same $10,000 can carry $40,000-$300,000 of orders and earn $2,000-$6,000.
That is the first thing to find out in the paper account. (The $25,000 pattern-day-trader minimum
no longer applies: FINRA replaced it with intraday margin rules from 2026-06-04.)

## Why this may not be real money (in order of weight)

1. **The orders have to be resting.** Reacting after the print does not work: a limit placed after
   the first trade at the limit earns +0.2 % / 0.0 %, and a market order at the next minute's open
   +1.9 % / +1.5 % (+1.1 % / +0.7 % if it pays that bar's high). So the rule needs a resting order
   in every eligible name: about 670 a day for 1.6 fills (0.3 % of orders fill; names with past
   sweeps fill up to 2 %, so ranking helps little). At $500 each that is $330k of open orders.
   Whether IBKR accepts them against a small margin account is untested; a cash account would not.
   The paper account can answer this in a day.
2. **Are the prints fills?** Many are one or two trades of 100-200 shares. If a displayed bid had
   been at the limit, the sell order would have met it; but odd lots, off-exchange prints, later
   cancelled trades or a bar built from a bad tick would not have. The strict-fill variant argues
   against queue position being the issue, not against bad prints. `scripts/explore.py
   tick-sample` wrote 400 fills; `scripts/massive.py ticks` (Mac) fetches the trades and quotes
   around them and `scripts/explore.py tick-check` reports how many were on-exchange and what
   the quote was.
3. **Selection.** The rule was shaped on the whole research window, in about 1,300 looks. The
   t-statistics are far beyond what that many looks produce by chance, and the three conditions are
   simple, but only the holdout can confirm it.
4. **The exit.** MOC in names this thin can move the close; the 60-minute exit assumes selling at
   a trade price in a wide market. Costs are a model.
5. **2020.** +0.5 %: in the March 2020 crash the drops kept going (86 fills on the worst day).

## Proposed next steps

1. Owner, on the Mac: `python3 scripts/massive.py ticks` (about 400 short requests), then
   `python scripts/explore.py tick-check` here.
2. Paper account: place a few hundred small resting limit orders before the open and see whether
   IBKR accepts them (buying power) and how the paper fills compare.
3. If both look right: fix the rule in a stage-3 pre-registration
   (`2026-11-15_preregistration_stage3_draft.md`), then run it once on the holdout, only with the
   owner's go-ahead.
4. Then a live pilot at the smallest size; one or two fills a day make it cheap to learn from.
