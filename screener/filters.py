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
    INDUSTRY_WHITELIST_BROAD,
    QUALITY_MAX_DILUTION_3YR_PCT,
    QUALITY_MIN_GROSS_MARGIN_PCT,
    QUALITY_MIN_REVENUE_GROWTH_PCT,
    TROUGH_MAX_GM_GIVEUP_PP,
    TROUGH_MIN_GROSS_MARGIN_PCT,
    TROUGH_MIN_REVENUE_GROWTH_PCT,
)


def is_excluded_industry(industry: str) -> bool:
    """Return True if an industry/sector string is in the exclusion list."""
    return industry.strip() in EXCLUDED_SECTORS


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

    # Filter out warrants, preferred shares, units, rights
    if "symbol" in df.columns:
        junk = df["symbol"].str.contains(
            r"[.-]|W$|WS$|U$|R$", regex=True, na=False
        )
        df = df[~junk]
    if "companyName" in df.columns:
        name_junk = df["companyName"].str.contains(
            r"Warrant|Rights|Preferred|Units|% NT |PFD",
            case=False, regex=True, na=False,
        )
        df = df[~name_junk]

    # Exclude biotech / pharma — but rescue gene editing companies
    # whose descriptions match target keywords (CRISPR, gene editing, etc.)
    if "description" in df.columns:
        has_target_desc = df["description"].fillna("").apply(_description_matches)
    else:
        has_target_desc = pd.Series(False, index=df.index)

    if "sector" in df.columns:
        excluded_by_sector = df["sector"].str.strip().isin(EXCLUDED_SECTORS)
        df = df[~excluded_by_sector | has_target_desc]
    if "industry" in df.columns:
        excluded_by_industry = df["industry"].str.strip().isin(EXCLUDED_SECTORS)
        df = df[~excluded_by_industry | has_target_desc]

    # Precise industries pass on industry name alone
    if "industry" in df.columns:
        precise_match = df["industry"].isin(INDUSTRY_WHITELIST)
    else:
        precise_match = pd.Series(False, index=df.index)

    if "description" in df.columns:
        keyword_match = df["description"].fillna("").apply(_description_matches)
    else:
        keyword_match = pd.Series(False, index=df.index)

    # Broad industries (Software - Infrastructure, IT Services, etc.)
    # only pass if description also matches target keywords
    if "industry" in df.columns:
        broad_match = df["industry"].isin(INDUSTRY_WHITELIST_BROAD) & keyword_match
    else:
        broad_match = pd.Series(False, index=df.index)

    df = df[precise_match | broad_match | keyword_match]
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


def _is_margin_holding_trough(metrics: dict[str, Any]) -> bool:
    """Is this a cyclical trough that kept its pricing, rather than a decline?

    The distinction is margin, not depth. When volume falls, an incumbent whose
    component nobody can design around keeps its price and gives up almost
    nothing at the gross line; a company losing its position discounts to hold
    share and the margin goes with the revenue.

    Requires the margin delta to be known — an unmeasurable trough is not
    given the benefit of the doubt.
    """
    rg = metrics.get("revenue_growth_pct")
    gm = metrics.get("gross_margin_pct")
    gm_delta = metrics.get("gross_margin_delta_yoy_pp")

    if rg is None or gm is None or gm_delta is None:
        return False
    if not (TROUGH_MIN_REVENUE_GROWTH_PCT <= rg <= QUALITY_MIN_REVENUE_GROWTH_PCT):
        return False
    if gm < TROUGH_MIN_GROSS_MARGIN_PCT:
        return False
    return gm_delta >= -TROUGH_MAX_GM_GIVEUP_PP


def passes_quality_floor(metrics: dict[str, Any]) -> bool:
    """Return True if a stock meets the minimum quality bar.

    Eliminates flat/shrinking businesses, commodity margins, and serial
    diluters — with one exception: a revenue trough that holds its gross
    margin is admitted, because that is the buy point for the vital-link
    archetype and the plain growth rule rejects it.
    """
    gm = metrics.get("gross_margin_pct")
    if gm is None or gm < QUALITY_MIN_GROSS_MARGIN_PCT:
        return False

    dil = metrics.get("dilution_3yr_pct")
    if dil is not None and dil > QUALITY_MAX_DILUTION_3YR_PCT:
        return False

    rg = metrics.get("revenue_growth_pct")
    if rg is not None and rg > QUALITY_MIN_REVENUE_GROWTH_PCT:
        return True

    return _is_margin_holding_trough(metrics)


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


def score_run_maturity(
    price: float | None,
    low_52w: float | None,
    high_52w: float | None,
    return_1y: float | None,
) -> float:
    """Score how much of a stock's run has already happened (0–100).

    Higher = already ran. Three components:

    1. Position in 52-week range (0–100): price near the high = already ran
    2. % above 52-week low, price-weighted: cheaper stocks get more leeway
       because a $5 stock tripling is still undiscovered, a $50 stock
       tripling is institutional-owned
    3. 1-year return magnitude: large 1Y gains = the move already happened

    Returns 0 if data is missing (benefit of the doubt).
    """
    if not price or price <= 0:
        return 0.0

    scores: list[float] = []

    # Component 1: Position in 52-week range
    if low_52w and high_52w and high_52w > low_52w:
        range_position = (price - low_52w) / (high_52w - low_52w)
        range_position = max(0.0, min(1.0, range_position))
        scores.append(range_position * 100)

    # Component 2: % above 52-week low, with price-weighted threshold
    # Cheaper stocks get a bigger % allowance before they're considered "run"
    # $5 stock: threshold ~400% above low before scoring 100
    # $50 stock: threshold ~200% above low
    # $200 stock: threshold ~100% above low
    if low_52w and low_52w > 0:
        pct_above_low = ((price - low_52w) / low_52w) * 100
        # Price-scaled threshold: lower-priced stocks need bigger moves
        threshold = max(80, 400 - (price * 4))
        maturity = min(100, (pct_above_low / threshold) * 100)
        scores.append(max(0.0, maturity))

    # Component 3: 1-year return magnitude
    if return_1y is not None:
        abs_return = abs(return_1y)
        # Scale: 0% = 0, 200%+ = 100, linear between
        ret_score = min(100, (abs_return / 200) * 100)
        # Only penalize positive returns — a stock down 50% hasn't "run".
        # This is deliberate: run maturity answers "has the move already
        # happened", and for a falling stock the honest answer is no. The risk
        # that it is falling for a reason belongs to the WPS deceleration guard
        # (see CONFIG["decel_guard"] in winner_pattern.py), not here — putting a
        # downtrend penalty in this function would conflate two questions and
        # double-count against genuine beaten-down setups.
        if return_1y < 0:
            ret_score = 0.0
        scores.append(ret_score)

    if not scores:
        return 0.0

    return round(sum(scores) / len(scores), 1)


def run_maturity_penalty(maturity_score: float) -> float:
    """Convert run maturity score (0–100) to a combined score penalty.

    Returns a multiplier (0.0–1.0) applied to the combined score.
    - Maturity 0–40: no penalty (1.0x)
    - Maturity 40–70: mild drag (1.0x → 0.85x)
    - Maturity 70–90: moderate drag (0.85x → 0.65x)
    - Maturity 90–100: heavy drag (0.65x → 0.50x)
    """
    if maturity_score <= 40:
        return 1.0
    if maturity_score <= 70:
        return 1.0 - 0.15 * ((maturity_score - 40) / 30)
    if maturity_score <= 90:
        return 0.85 - 0.20 * ((maturity_score - 70) / 20)
    return 0.65 - 0.15 * ((maturity_score - 90) / 10)


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
