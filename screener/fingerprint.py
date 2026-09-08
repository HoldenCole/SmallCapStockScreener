"""Fingerprint scoring — compare a stock to reference winners at their inflection point.

The reference stocks are a specification of intent, not a training sample: each
one is a hand-picked statement of "something shaped like this". Scoring therefore
matches a candidate against the NEAREST reference rather than against a merged
envelope of all of them.

That matters because the set deliberately spans more than one archetype. AAOI at
its inflection was a $300M company growing 95%; Corning at its 2023 trough was a
$26B company whose revenue had just fallen 11%. Both are the thing we want — a
vital link in a secular growth chain — but a single min/max band containing both
spans -11% to +95% growth and $300M to $26B, which is to say it contains
everything and discriminates nothing. Taking the best per-reference match keeps
each archetype recognisable and makes the score robust to any one reference being
unusual, which the min/max envelope never was.
"""

import json
import math
import os
from typing import Any

# Distance at which similarity on a metric decays to zero. These are judgement
# calls chosen for interpretability — a company 40pp off on growth, or 20pp off
# on gross margin, is a different kind of business — not values fitted to
# returns. Market cap is compared in orders of magnitude, since $300M against
# $26B is meaningless on a linear scale.
TOLERANCE = {
    "revenue_growth_pct": 40.0,
    "gross_margin_pct": 20.0,
    "insider_ownership_pct": 15.0,
    "market_cap_log10": 1.0,
}


_REF_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "reference_stocks.json")


def load_reference_stocks() -> list[dict[str, Any]]:
    """Load reference stocks from JSON."""
    with open(_REF_PATH) as f:
        return json.load(f)["reference_stocks"]


def compute_reference_averages(refs: list[dict[str, Any]] | None = None) -> dict[str, float]:
    """Average pre-run metrics across all reference stocks."""
    if refs is None:
        refs = load_reference_stocks()
    n = len(refs)
    if n == 0:
        return {}
    return {
        "revenue_growth_pct": sum(r["pre_run_revenue_growth_pct"] for r in refs) / n,
        "gross_margin_pct": sum(r["pre_run_gross_margin_pct"] for r in refs) / n,
        "insider_ownership_pct": sum(r["pre_run_insider_ownership_pct"] for r in refs) / n,
        "dilution_3yr_pct": sum(r["pre_run_dilution_3yr_pct"] for r in refs) / n,
        "market_cap_M": sum(r["pre_run_market_cap_M"] for r in refs) / n,
    }


def compute_ideal_ranges(refs: list[dict[str, Any]] | None = None) -> dict[str, tuple[float, float]]:
    """Min/max across reference stocks for each metric — the 'ideal range'."""
    if refs is None:
        refs = load_reference_stocks()
    return {
        "revenue_growth_pct": (
            min(r["pre_run_revenue_growth_pct"] for r in refs),
            max(r["pre_run_revenue_growth_pct"] for r in refs),
        ),
        "gross_margin_pct": (
            min(r["pre_run_gross_margin_pct"] for r in refs),
            max(r["pre_run_gross_margin_pct"] for r in refs),
        ),
        "insider_ownership_pct": (
            min(r["pre_run_insider_ownership_pct"] for r in refs),
            max(r["pre_run_insider_ownership_pct"] for r in refs),
        ),
        "dilution_3yr_pct": (
            min(r["pre_run_dilution_3yr_pct"] for r in refs),
            max(r["pre_run_dilution_3yr_pct"] for r in refs),
        ),
        "market_cap_M": (
            min(r["pre_run_market_cap_M"] for r in refs),
            max(r["pre_run_market_cap_M"] for r in refs),
        ),
    }


def _proximity(value: float, low: float, high: float) -> float:
    """Score 0–100 based on how well *value* falls within [low, high].

    Inside the range → 100.  Farther away → decays toward 0.
    """
    if low <= value <= high:
        return 100.0
    if value < low:
        dist = low - value
        span = high - low if high != low else 1
        return max(0.0, 100.0 - (dist / span) * 100)
    # value > high
    dist = value - high
    span = high - low if high != low else 1
    return max(0.0, 100.0 - (dist / span) * 100)


def _dilution_fp_score(dilution_pct: float | None) -> float:
    """Lower dilution is better.  <5% = 100, >30% = 0."""
    if dilution_pct is None:
        return 50.0
    if dilution_pct <= 5:
        return 100.0
    if dilution_pct >= 30:
        return 0.0
    return round(100 - ((dilution_pct - 5) / 25) * 100, 1)


def _similarity(value: float, target: float, tolerance: float) -> float:
    """100 when equal, decaying linearly to 0 at `tolerance` away."""
    return max(0.0, 100.0 - abs(value - target) / tolerance * 100.0)


def score_against_reference(
    stock_metrics: dict[str, Any], ref: dict[str, Any]
) -> float:
    """How closely one candidate resembles one reference stock, 0–100."""
    scores: list[float] = []

    rg = stock_metrics.get("revenue_growth_pct")
    scores.append(
        _similarity(rg, ref["pre_run_revenue_growth_pct"],
                    TOLERANCE["revenue_growth_pct"]) if rg is not None else 0.0
    )

    gm = stock_metrics.get("gross_margin_pct")
    scores.append(
        _similarity(gm, ref["pre_run_gross_margin_pct"],
                    TOLERANCE["gross_margin_pct"]) if gm is not None else 0.0
    )

    io = stock_metrics.get("insider_ownership_pct")
    scores.append(
        _similarity(io, ref["pre_run_insider_ownership_pct"],
                    TOLERANCE["insider_ownership_pct"]) if io is not None else 0.0
    )

    # Dilution is a preference, not a resemblance — we do not want a candidate
    # to look MORE diluted just because a reference was, so this stays an
    # absolute "lower is better" curve rather than a distance.
    scores.append(_dilution_fp_score(stock_metrics.get("dilution_3yr_pct")))

    mc = stock_metrics.get("market_cap_M")
    ref_mc = ref.get("pre_run_market_cap_M")
    if mc is not None and mc > 0 and ref_mc:
        scores.append(_similarity(math.log10(mc), math.log10(ref_mc),
                                  TOLERANCE["market_cap_log10"]))
    else:
        scores.append(0.0)

    return sum(scores) / len(scores)


def compute_fingerprint_score(
    stock_metrics: dict[str, Any],
    refs: list[dict[str, Any]] | None = None,
) -> float:
    """Return 0–100: how closely this stock resembles its nearest reference.

    Best match, not average match — resembling one archetype strongly is the
    signal; resembling the midpoint of several unrelated archetypes is not.

    Expected keys in stock_metrics:
        revenue_growth_pct, gross_margin_pct, insider_ownership_pct,
        dilution_3yr_pct, market_cap_M
    """
    score, _ = compute_fingerprint_match(stock_metrics, refs)
    return score


def compute_fingerprint_match(
    stock_metrics: dict[str, Any],
    refs: list[dict[str, Any]] | None = None,
) -> tuple[float, str]:
    """Fingerprint score plus the ticker of the reference it matched."""
    if refs is None:
        refs = load_reference_stocks()
    if not refs:
        return 0.0, ""
    scored = [(score_against_reference(stock_metrics, r), r["ticker"])
              for r in refs]
    best, ticker = max(scored, key=lambda x: x[0])
    return round(best, 1), ticker
