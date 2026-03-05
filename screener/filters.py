"""Hard filters, sanity checks, and composite scoring logic."""

import re
from typing import Any

import pandas as pd

from config import (
    DEFAULT_WEIGHTS,
    DESCRIPTION_KEYWORDS,
    DESCRIPTION_KEYWORDS_STRICT,
    EXCLUDED_SECTORS,
    INDUSTRY_WHITELIST,
)


def _description_matches(desc: str) -> bool:
    """Check if a company description matches our target themes.

    Uses substring matching for multi-word phrases and word-boundary
    regex for short/ambiguous terms to avoid false positives.
    """
    if not desc:
        return False
    desc_lower = desc.lower()

    # Multi-word phrases: safe to substring-match
    for kw in DESCRIPTION_KEYWORDS:
        if kw.lower() in desc_lower:
            return True

    # Short terms: require word boundaries to avoid matching
    # "AI" in "mountain" or "space" in "workspace"
    for kw in DESCRIPTION_KEYWORDS_STRICT:
        if re.search(rf"\b{re.escape(kw.lower())}\b", desc_lower):
            return True

    return False


def apply_hard_filters(df: pd.DataFrame) -> pd.DataFrame:
    """Remove stocks that fail hard filter criteria.

    Expects columns: sector, industry, country, isActivelyTrading, description.
    """
    if df.empty:
        return df

    # Must be US and actively trading
    if "country" in df.columns:
        df = df[df["country"].str.upper() == "US"]
    if "isActivelyTrading" in df.columns:
        df = df[df["isActivelyTrading"] == True]  # noqa: E712

    # Exclude biotech / pharma
    if "sector" in df.columns:
        df = df[~df["sector"].str.strip().isin(EXCLUDED_SECTORS)]
    if "industry" in df.columns:
        df = df[~df["industry"].str.strip().isin(EXCLUDED_SECTORS)]

    # Industry whitelist OR description keyword match
    if "industry" in df.columns:
        industry_match = df["industry"].isin(INDUSTRY_WHITELIST)
    else:
        industry_match = pd.Series(False, index=df.index)

    if "description" in df.columns:
        keyword_match = df["description"].fillna("").apply(_description_matches)
    else:
        keyword_match = pd.Series(False, index=df.index)

    df = df[industry_match | keyword_match]
    return df.reset_index(drop=True)


def apply_sanity_filters(metrics: dict[str, Any]) -> bool:
    """Return True if metrics look sane enough to score, False to skip.

    Catches garbage data like -3000% gross margins or 4000% dilution.
    """
    gm = metrics.get("gross_margin_pct")
    if gm is not None and gm < -100:
        return False

    dil = metrics.get("dilution_3yr_pct")
    if dil is not None and dil > 500:
        return False

    rg = metrics.get("revenue_growth_pct")
    if rg is not None and rg < -95:
        return False

    return True


# ------------------------------------------------------------------
# Individual signal scorers (each returns 0.0 – 100.0)
# ------------------------------------------------------------------


def _score_revenue_growth(growth_pct: float | None) -> float:
    """Higher growth -> higher score. >50% = 100."""
    if growth_pct is None:
        return 0.0
    if growth_pct >= 50:
        return 100.0
    if growth_pct <= 0:
        return 0.0
    return round((growth_pct / 50) * 100, 1)


def _score_gross_margin(margin_pct: float | None) -> float:
    """>40% = 100. Scales linearly down to 0% = 0."""
    if margin_pct is None:
        return 0.0
    if margin_pct >= 40:
        return 100.0
    if margin_pct <= 0:
        return 0.0
    return round((margin_pct / 40) * 100, 1)


def _score_dilution(dilution_3yr_pct: float | None) -> float:
    """Lower dilution = better. <5% = 100, >30% = 0."""
    if dilution_3yr_pct is None:
        return 50.0  # unknown -> neutral
    if dilution_3yr_pct <= 5:
        return 100.0
    if dilution_3yr_pct >= 30:
        return 0.0
    return round(100 - ((dilution_3yr_pct - 5) / 25) * 100, 1)


def _score_insider_ownership(insider_pct: float | None) -> float:
    """>10% = 100. Scales linearly."""
    if insider_pct is None:
        return 0.0
    if insider_pct >= 10:
        return 100.0
    if insider_pct <= 0:
        return 0.0
    return round((insider_pct / 10) * 100, 1)


def _score_revenue_acceleration(acceleration: float | None) -> float:
    """Positive acceleration = growth is speeding up. >20pp = 100."""
    if acceleration is None:
        return 0.0
    if acceleration >= 20:
        return 100.0
    if acceleration <= -20:
        return 0.0
    return round(((acceleration + 20) / 40) * 100, 1)


def score_stock(
    metrics: dict[str, Any],
    weights: dict[str, float] | None = None,
) -> float:
    """Compute composite score (0-100) from stock metrics.

    Expected keys in metrics:
        revenue_growth_pct, gross_margin_pct, dilution_3yr_pct,
        insider_ownership_pct, revenue_acceleration_pct
    """
    w = weights or DEFAULT_WEIGHTS

    components = {
        "revenue_growth": _score_revenue_growth(metrics.get("revenue_growth_pct")),
        "gross_margin": _score_gross_margin(metrics.get("gross_margin_pct")),
        "dilution": _score_dilution(metrics.get("dilution_3yr_pct")),
        "insider_ownership": _score_insider_ownership(
            metrics.get("insider_ownership_pct")
        ),
        "revenue_acceleration": _score_revenue_acceleration(
            metrics.get("revenue_acceleration_pct")
        ),
    }

    total = sum(components[k] * w.get(k, 0) for k in components)
    return round(total, 1)
