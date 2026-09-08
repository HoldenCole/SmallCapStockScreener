"""Statement-basis reconciliation.

Year-over-year comparisons assume both periods are stated on the same basis.
That assumption breaks after a divestiture: the current quarter is restated to
continuing operations while the year-ago quarter is still the consolidated
figure, and comparing them produces growth and margin numbers that are not
merely imprecise but inverted.

Clearfield is the case that prompted this. For its fiscal Q3 2026 the feed
returns $43.9M revenue at 31.8% gross margin — both correct, both continuing
operations — against a year-ago quarter of $49.9M at 30.5%, which is
consolidated and pre-divestiture. The screener therefore read revenue -12.1%
with margin +1.3pp. The company reported +13% revenue with margin down 3.5pp.
Both signs flipped, and the flipped version is precisely the margin-holding
trough signature, so the artifact ranked the name first overall.

There is no independent source here to reconcile against, so these checks look
for the fingerprints a restatement leaves in the series itself. A candidate
that trips any of them has its year-over-year metrics suppressed rather than
scored — the trough path requires both revenue growth and a margin delta, so
suppressing them closes it automatically.
"""

from __future__ import annotations

from typing import Any

# A discontinued-operations line larger than this share of quarterly revenue
# means the company is carving something out, so periods either side of it are
# not comparable.
DISCONTINUED_OPS_REVENUE_FRAC = 0.01

# A quarter whose gross margin sits this far from the median of its neighbours
# is a restatement artifact, not a business event. Clearfield's Q4 FY2025 shows
# 73.5% against neighbours near 31%.
GM_OUTLIER_PP = 15.0

# Gross margin moving this far between ADJACENT quarters. Distance from the
# window median misses a series that steps to a new level rather than spiking:
# Enphase went 35.5% to 60.0% in one quarter, a 24.5pp move, but sits only
# 13.1pp from its own median and so passed the median test.
GM_SEQUENTIAL_SWING_PP = 20.0

# Sum of four quarters should reconcile to the annual figure. Clearfield's
# FY2025 quarters sum to $144.4M against an annual $150.1M, a 3.8% gap, because
# the two are stated on different bases.
ANNUAL_RECONCILE_FRAC = 0.02


def _gm(stmt: dict[str, Any]) -> float | None:
    rev = stmt.get("revenue") or 0
    gp = stmt.get("grossProfit")
    if not rev or rev <= 0 or gp is None:
        return None
    return gp / rev * 100


def check_statement_consistency(
    income_quarterly: list[dict[str, Any]],
    income_annual: list[dict[str, Any]] | None = None,
    window: int = 5,
) -> tuple[list[str], list[str]]:
    """Reasons the year-over-year comparison may be unreliable.

    Returns (hard, soft).

    Hard issues are direct evidence that the two periods are stated on
    different bases — a discontinued-operations line inside the comparison
    window, or quarters that do not sum to their own annual figure. Growth and
    margin computed across such a boundary can be wrong in sign, not just in
    magnitude, so the candidate should not be scored at all.

    Soft issues mean the margin series is too erratic to support the
    "margin held through the decline" test, without proving a basis change.
    Some businesses genuinely swing that way — an ethanol producer's gross
    margin moves 15pp on feedstock prices with nothing restated. These close
    the trough path but leave an otherwise-growing company scoreable.

    `window` is how many quarters the comparison spans; 5 covers the current
    quarter through the year-ago one it is measured against.

    Empty lists are not proof the data is clean — only that none of these
    particular fingerprints is present.
    """
    hard: list[str] = []
    soft: list[str] = []
    issues = hard  # direct evidence accumulates here
    if not income_quarterly:
        return (["no quarterly statements"], [])

    span = income_quarterly[:window]

    # 1. A divestiture or carve-out anywhere in the comparison window.
    for s in span:
        rev = s.get("revenue") or 0
        disc = s.get("netIncomeFromDiscontinuedOperations") or 0
        if rev > 0 and abs(disc) > rev * DISCONTINUED_OPS_REVENUE_FRAC:
            issues.append(
                f"discontinued operations in {s.get('date')} "
                f"({disc/1e6:+.1f}M on {rev/1e6:.1f}M revenue)"
            )
            break

    # 2. A gross margin that jumps out of line with its neighbours.
    margins = [(s.get("date"), _gm(s)) for s in span]
    known = [m for _, m in margins if m is not None]
    if len(known) >= 3:
        srt = sorted(known)
        median = srt[len(srt) // 2]
        for date, m in margins:
            if m is not None and abs(m - median) > GM_OUTLIER_PP:
                soft.append(
                    f"gross margin {m:.1f}% in {date} is {abs(m - median):.0f}pp "
                    f"off the {median:.1f}% median of the comparison window"
                )
                break

    # 2b. Gross margin stepping to a new level between adjacent quarters.
    for (d_new, m_new), (d_old, m_old) in zip(margins, margins[1:]):
        if m_new is None or m_old is None:
            continue
        if abs(m_new - m_old) > GM_SEQUENTIAL_SWING_PP:
            soft.append(
                f"gross margin moved {abs(m_new - m_old):.0f}pp between "
                f"{d_old} ({m_old:.1f}%) and {d_new} ({m_new:.1f}%)"
            )
            break

    # 3. Quarters that do not sum to the annual figure they belong to.
    if income_annual:
        by_year: dict[Any, list[dict]] = {}
        for s in income_quarterly:
            by_year.setdefault(s.get("fiscalYear"), []).append(s)
        for a in income_annual:
            qs = by_year.get(a.get("fiscalYear"), [])
            arev = a.get("revenue") or 0
            if len(qs) != 4 or arev <= 0:
                continue
            qsum = sum((q.get("revenue") or 0) for q in qs)
            gap = abs(qsum - arev) / arev
            if gap > ANNUAL_RECONCILE_FRAC:
                issues.append(
                    f"FY{a.get('fiscalYear')} quarters sum to {qsum/1e6:.1f}M "
                    f"against an annual {arev/1e6:.1f}M ({gap*100:.1f}% apart)"
                )
                break

    return hard, soft
