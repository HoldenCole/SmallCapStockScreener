# Backtest findings

Results from `backtest/run.py` and the quadrant analysis. Criteria were locked
in METHODOLOGY.md before running. Where a result failed its own pre-registered
test it is recorded as failed rather than quietly reframed.

---

## 1. Exit rules: buy-and-hold beats every rule tested

4,002 positions, 69 tickers, 58 entry dates every 4 weeks, 2021–2026.

| Rule | mean | median | top-decile share of gains |
|---|---|---|---|
| **hold 252d (baseline)** | **+24.4%** | +4.8% | **28%** |
| stop −25% | +16.2% | −13.7% | 38% |
| target +50% | +13.7% | +12.9% | 16% |
| scale-out ladder | +11.3% | +15.9% | 16% |
| target +30% | +11.0% | +30.2% | 15% |

Eight rules passed all four locked criteria and every one loses 11–15pp of mean
return. **Criterion C1 ("beats buy-and-hold on median") is degenerate for
threshold exits**: a "+X% target" reports a median of almost exactly +X%
whenever more than half of positions touch it, so it measures the threshold
rather than the rule. The criterion was left in place and the flaw disclosed in
the output, because replacing the yardstick after seeing results is what the
methodology forbids.

Mechanism is the last column: buy-and-hold draws 28% of gains from the top
decile of positions; target rules cut that to 15–16%. They amputate the right
tail, which is the entire premise of screening for pre-run compounders.

**Verdict: adopt nothing. Do not add profit targets.**

## 2. Naive SPY 50/200 regime gate: rejected

Aggregate lift looked real (+29.7% gated vs +24.4% ungated on half the
entries). Decomposed by year, regime-ON beat regime-OFF in **0 of 4 years**
where both states occurred. The entire lift came from the gate mechanically
excluding 2021, when the regime was constantly OFF and the universe fell 16.6%.
A composition artifact, not a regime effect. The decomposition is now built into
`run.py` so the same trap cannot be walked into twice.

## 3. Four-quadrant classifier (ported from IBKRTradingBot)

`backtest/quadrant.py` is a faithful port of `src/regime/quadrant.py` from
branch `claude/trading-bot-cl1-uso-spread-02qa7h`. Port validated against the
published REGIMES.md table:

| Quadrant | months (theirs / port) | SPY theirs / port | QQQ theirs / port |
|---|---|---|---|
| G+I− GROWTH | 75 / 80 | +14.9% / +14.3% | +22.3% / +22.8% |
| G+I+ REFLATION | 108 / 89 | +7.4% / +7.3% | +9.5% / +11.0% |
| G−I+ STAGFLATION | 19 / 18 | −10.7% / −11.3% | −6.0% / −4.7% |
| G−I− DEFLATION | 30 / 32 | +31.4% / +23.4% | +44.3% / +34.6% |

(Theirs is next-month annualised, the port is forward 252d, so DEFLATION — whose
rebounds are timing-sensitive — differs most. Everything else matches closely.)

### Small caps by quadrant (IWM, 219 entry months, 2007–2026)

| Quadrant | n | IWM fwd 252d |
|---|---|---|
| G+I− GROWTH | 80 | +11.3% |
| G+I+ REFLATION | 89 | +5.6% |
| G−I+ STAGFLATION | 18 | **−12.1%** |
| G−I− DEFLATION | 32 | **+28.1%** |

GROWTH vs everything else: +2.9pp mean, +1.4pp median. Era-stable in sign
(2007–2015 +1.6pp, 2016–2026 +3.6pp) but small.

Note DEFLATION is the **best** quadrant for small caps (+28.1%), so the
`defense` family's "stand down in STAGFLATION + DEFLATION" mapping should not be
copied across to this screener. That matches the caveat already in REGIMES.md
about DEFLATION hiding post-crash rebounds.

### Stand down in STAGFLATION only — tested and REJECTED

| | n | IWM mean |
|---|---|---|
| All months | 219 | +9.5% |
| Ex-STAGFLATION | 201 | +11.5% |
| STAGFLATION only | 18 | −12.1% |

+1.9pp for sitting out 8% of months. But the era check kills it:

- 2007–2015: lift **+3.8pp** (stagflation months averaged −27.8%)
- 2016–2026: lift **+0.3pp** (stagflation months averaged **+7.4%**)

And the 18 months are **5 distinct episodes** (2008, 2011, 2022, 2023, 2025),
not 18 independent observations — essentially one 2008 result. Fails the
registry's era-stability standard. **Not adopted.**

## 4. run_maturity — re-tested, weaker than first claimed

Earlier QA reported run_maturity as the model's best signal from n=59 in a
single window (low maturity beat high by +19.6pp). Re-tested on n=2,908:

| | n | mean | median |
|---|---|---|---|
| maturity > 40 | 788 | +19.8% | +5.6% |
| maturity ≤ 40 | 2,120 | +35.3% | +7.6% |

Direction survives (+15.5pp mean) but it is **tail-driven** — the median spread
is only +2.0pp — and it works in 3 of 5 years (2023 −3.4pp, 2026 −0.9pp).

This is *not* contradicted by Entry 68 in PORTFOLIOS.md ("no era-stable penalty
for buying the extended book"), which tested index-level extension on a rotating
multi-asset book. That is macro momentum, which is a different signal at a
different level of aggregation from a single stock's position in its own 52-week
range. Both can be true. But the effect here is weaker and less era-stable than
the earlier claim implied, and should be treated as a tilt, not a gate.

## 5. The characterisation that actually matters

Same entry dates, forward 252 days:

| | mean | median | win rate | return / drawdown |
|---|---|---|---|---|
| SPY | +14.6% | +16.3% | 86% | **1.17** |
| Screened universe | +24.4% | +4.8% | 55% | **0.12** |

The screened names beat SPY on mean by +9.9pp, lose on median by −11.5pp, and
have roughly **ten times worse risk-adjusted return**. This is a lottery-ticket
distribution: the typical name badly underperforms the index and a few winners
carry everything. The mean excess is also an upper bound, since these names were
selected on 2026 characteristics and backtested from 2021.

The implication is consistent across every test above: the edge, if it exists,
is in **holding winners and sizing positions**, not in timing entries or exits.

## Still untested

Whether the screen picks good names. That needs point-in-time fundamentals to
reconstruct historical screens, which needs an FMP key. It currently rests on
116 positions in one quarter.

---

## 6. Asymmetric fingerprint similarity — tested and REJECTED

Proposal: stop penalising a candidate for exceeding a reference. Revenue
growth above the reference would score 100 outright (a screener hunting growth
inflections should not mark down a 58% grower for failing to resemble a 38%
one), and insider ownership above the range would taper gently rather than
falling to zero at 45%.

Both are reasonable on their face. Both make the ranking worse, and they
compound. Tested on identical rows, 2,391 point-in-time evaluations, quartiles
ranked within each date:

| variant | Q1 median | Q4 median | spread | dates correct | small | large |
|---|---|---|---|---|---|---|
| **both symmetric** | **+12.2%** | −7.7% | **+19.8pp** | **17/18** | +27.7 | +12.5 |
| growth asymmetric only | +9.1% | −6.9% | +16.1pp | 16/18 | +21.6 | +8.4 |
| insider gradient only | +6.9% | −3.7% | +10.6pp | 14/18 | +10.2 | +7.8 |
| both asymmetric | +3.3% | −2.3% | +5.6pp | 10/18 | +3.7 | +1.9 |

Monotone degradation, every metric, every step away from symmetric.

Confirmed independently on the clean run with insider ownership dropped, where
only the growth change is active: 15/18 dates and a +8.3pp large-cap spread
symmetric, against 13/18 and −0.2pp asymmetric.

Why the symmetric version is right. Deviation in either direction is
informative, which is not obvious until measured. Growth far above the
references is usually a one-off comparison, a rebound, or an acquisition
rather than a durable inflection — consistent with revenue growth correlating
negatively with forward returns everywhere else in this analysis. Insider
ownership far above the range is usually a retained sponsor stake on a recent
IPO, meaning thin float and overhang, rather than founder conviction.

The similarity function is not a proxy for "more is better". It asks whether a
candidate looks like something that already worked, and both tails are answers.

### The KRMN case that prompted it

Karman Holdings scored a perfect 100 composite and a 55.8 fingerprint, so it
led the Breakout tier under the old weights and fell out under the new ones.
Its insider component scored zero against every reference.

That is not a symmetry problem. KRMN is 58.2% growth, 43% gross margin, 78%
insider ownership, $5.4B — and no reference combines high growth with high
margin and concentrated ownership. LITE has the margin but neither the growth
nor the ownership; RKLB has the growth and higher ownership but half the
margin. It falls in a genuine gap in the reference set.

The principled fix, if that shape is believed to be a winner, is to add a
reference with it — the reference set is a specification of intent, and gaps in
it are gaps in the specification. Breaking the scoring function to admit one
name is not.
