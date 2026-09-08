# Backtest Methodology — locked before results

Written before any backtest was run. Criteria are fixed here so results are
judged against a standard set in advance, not one fitted to what came back.
Follows the discipline in the commodity trend spec: signal definitions locked,
no post-hoc parameter tuning, held-out periods, interpretable findings.

## The two questions are separate

**Q1 — Do exit rules add value?** Testable now on a large sample.
**Q2 — Does the screen pick good names?** Needs point-in-time fundamentals
(FMP) to reconstruct historical screens. Currently limited to the four stored
workbook snapshots (116 positions, Mar–Jun 2026), which is far too small.

This harness answers Q1. It does not claim to answer Q2.

## Q1 design

**Universe.** The 69 tickers that appeared in any stored screen output. These
are a proxy for "the kind of name this screener surfaces."

**Entries.** Every 4 weeks from 2021-09 to 2026-03, for every ticker with data
on that date. Positions are equal-weight and independent — no portfolio
construction, no position sizing. One name, one entry date, one exit rule.

**Exit rules (locked grid, tested once).**

| Family | Parameters |
|---|---|
| Buy & hold | 63 / 126 / 252 trading days |
| Fixed profit target | +15 / 20 / 25 / 30 / 40 / 50% |
| Stop loss | −10 / −15 / −20 / −25% |
| Trailing stop (% off peak) | 10 / 15 / 20 / 25% |
| ATR trailing stop | 1.5× / 2.0× / 3.0× ATR20, ratcheting |
| Time stop | 30 / 60 / 90 / 180 calendar days |
| Scale-out ladder | +25% sell ½, +50% sell ¼, remainder trails 20% |
| Target + stop | {20,30,40}% target paired with {−15,−20}% stop |

All rules cap the hold at 252 trading days so every position closes and results
are comparable.

**Overlays (entry gates, from the IBKR strategy spec).**
- *Regime*: enter only when SPY close > SMA50 **and** SMA50 > SMA200
  (Variant 1 definition). Policy A — a mid-trade regime flip does **not**
  force an exit. Fail closed: no regime data means no entry.
- *Momentum*: enter only when the name's 12-month-minus-1-month return is
  positive. Vol-adjusted variant: 12m return / 12m realised vol in the top
  half of the universe that day.

These are explicitly **not** the strategy. They are tested as a layer on top of
whatever the screen produces.

## Decision criteria (locked)

An exit rule earns its place only if **all** of:

1. Beats buy-and-hold-252d on **median** position return (median, not mean —
   one lucky 10x should not carry a rule).
2. Beats buy-and-hold on return / max-drawdown.
3. Works in **≥3 of 5** calendar years. A rule that only works in 2022 is a
   bear-market artifact, not an exit rule.
4. Survives a random 50/50 split of the universe — consistent sign on both
   halves.

Anything failing one criterion is reported as "interesting, not adopted."
Anything failing two or more is rejected.

## Known limitations — stated before results, not after

1. **Selection bias in the universe.** These 69 names were chosen *because* of
   their 2026 characteristics, then backtested from 2021. That inflates the
   absolute return level of every rule. The defence is that Q1 asks which exit
   rule ranks best, and the bias applies near-equally to all rules on the same
   positions. It does **not** make the return levels themselves trustworthy.
2. **Survivorship.** Delisted names never entered any screen, so they are
   absent. Real-world results would be worse.
3. **No costs.** No commission, no slippage, no spread. Rules that trade more
   are flattered.
4. **Daily closes only.** Intraday stop and target fills are modelled at the
   close of the day the condition triggers, not at the trigger price. This
   understates stop-loss damage and overstates target capture on gaps.
5. **Overlapping positions.** The same ticker is entered many times on a
   rolling schedule, so positions are not independent observations. Reported
   p-values would be overconfident and are therefore not reported for Q1.
