"""Fingerprint scoring — compare a stock to reference winners at their inflection point."""

import json
import os
from typing import Any


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


def compute_fingerprint_score(
    stock_metrics: dict[str, Any],
    refs: list[dict[str, Any]] | None = None,
) -> float:
    """Return 0–100 score: how similar does this stock look to pre-run winners?

    Expected keys in stock_metrics:
        revenue_growth_pct, gross_margin_pct, insider_ownership_pct,
        dilution_3yr_pct, market_cap_M
    """
    ideal = compute_ideal_ranges(refs)

    scores: list[float] = []

    # Revenue growth proximity
    rg = stock_metrics.get("revenue_growth_pct")
    if rg is not None:
        scores.append(_proximity(rg, *ideal["revenue_growth_pct"]))
    else:
        scores.append(0.0)

    # Gross margin proximity
    gm = stock_metrics.get("gross_margin_pct")
    if gm is not None:
        scores.append(_proximity(gm, *ideal["gross_margin_pct"]))
    else:
        scores.append(0.0)

    # Insider ownership proximity
    io = stock_metrics.get("insider_ownership_pct")
    if io is not None:
        scores.append(_proximity(io, *ideal["insider_ownership_pct"]))
    else:
        scores.append(0.0)

    # Dilution — lower is better
    scores.append(_dilution_fp_score(stock_metrics.get("dilution_3yr_pct")))

    # Market cap in the right zone
    mc = stock_metrics.get("market_cap_M")
    if mc is not None:
        scores.append(_proximity(mc, *ideal["market_cap_M"]))
    else:
        scores.append(0.0)

    return round(sum(scores) / len(scores), 1) if scores else 0.0
