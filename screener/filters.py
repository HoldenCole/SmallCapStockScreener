"""Hard filters, sanity checks, and composite scoring logic."""

import re
from typing import Any

import pandas as pd

from config import (
    DEFAULT_WEIGHTS,
    DESCRIPTION_KEYWORDS,
    DESCRIPTION_KEYWORDS_STRICT,
    DESCRIPTION_EXCLUSION_KEYWORDS,
    EXCLUDED_EXCHANGE_MARKERS,
    EXCLUDED_SECTORS,
    INDUSTRY_WHITELIST,
    INDUSTRY_WHITELIST_BROAD,
    QUALITY_MAX_DILUTION_3YR_PCT,
    SANITY_MAX_GROSS_MARGIN_PCT,
    QUALITY_MIN_GROSS_MARGIN_PCT,
    QUALITY_MIN_REVENUE_GROWTH_PCT,
    TROUGH_GIVEUP_RATIO_ZERO,
    TROUGH_GROWTH_CREDIT_MAX,
    TROUGH_MAX_GM_GIVEUP_PP,
    TROUGH_MAX_REVENUE_GROWTH_PCT,
    TROUGH_MIN_GROSS_MARGIN_PCT,
    TROUGH_MIN_REVENUE_GROWTH_PCT,
)


def is_excluded_industry(industry: str) -> bool:
    """Return True if an industry/sector string is in the exclusion list."""
    return industry.strip() in EXCLUDED_SECTORS


def _description_excluded(desc: str) -> bool:
    """Does the description say the company's primary business is excluded?

    Callers must only apply this to candidates admitted on a description
    keyword. A whitelisted-industry company naming oil and gas among its end
    markets is not an oil and gas company.
    """
    if not desc:
        return False
    low = desc.lower()
    return any(kw.lower() in low for kw in DESCRIPTION_EXCLUSION_KEYWORDS)


def is_excluded_exchange(exchange: str) -> bool:
    """OTC, pink sheet and grey market are not major exchanges."""
    if not exchange:
        return False
    up = exchange.upper()
    return any(marker in up for marker in EXCLUDED_EXCHANGE_MARKERS)


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

    # Keyword-only entrants are held to their own description: if the text that
    # let them in also says their primary business is excluded, they are out.
    # Companies qualifying on a whitelisted industry are exempt, so an aerospace
    # supplier listing oil and gas as an end market is unaffected.
    if "description" in df.columns:
        keyword_only = keyword_match & ~precise_match
        desc_excluded = df["description"].fillna("").apply(_description_excluded)
        df = df[~(keyword_only & desc_excluded)]
        precise_match = precise_match[df.index]
        broad_match = broad_match[df.index]
        keyword_match = keyword_match[df.index]

    df = df[precise_match | broad_match | keyword_match]

    # Major exchanges only. Checked last so the reason is unambiguous in the
    # candidate log rather than being masked by an industry mismatch.
    for col in ("exchangeShortName", "exchange"):
        if col in df.columns:
            df = df[~df[col].fillna("").apply(is_excluded_exchange)]
            break

    return df.reset_index(drop=True)


def apply_sanity_filters(metrics: dict[str, Any]) -> bool:
    """Return True if metrics look sane enough to score, False to skip.

    Catches garbage data like -3000% gross margins or 4000% dilution.
    """
    gm = metrics.get("gross_margin_pct")
    if gm is not None and gm < -100:
        return False
    # A gross margin at or above 99.5% means the feed reported no cost of
    # revenue, not that the business has none. The first live run scored
    # Charter Communications and Suburban Propane — a cable operator and a
    # propane distributor — at exactly 100%, and the bogus figure carried them
    # through the trough test's margin leg.
    if gm is not None and gm >= SANITY_MAX_GROSS_MARGIN_PCT:
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
    # Must be an actual decline. The first live run admitted 34 mature
    # businesses sitting at +2% to +5% growth — EPAM, Cognizant, Verisk,
    # Republic Services — because a company under no stress holds its margin
    # trivially. Stable margin is only evidence of pricing power when volume
    # actually fell.
    if not (TROUGH_MIN_REVENUE_GROWTH_PCT <= rg <= TROUGH_MAX_REVENUE_GROWTH_PCT):
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


def score_margin_resilience(
    growth_pct: float | None, gm_delta_pp: float | None
) -> float:
    """0–100: how much pricing power a business kept while revenue fell.

    Measured as gross margin conceded per point of revenue decline. An
    incumbent whose component nobody can design around holds its price when
    volume drops; a company losing its position discounts to defend share and
    the margin falls with the revenue.

    Zero for a growing business — this answers a question that only arises in a
    decline — and zero when the margin delta is unknown, so an unmeasurable
    trough gets no benefit of the doubt.
    """
    # Same band as the quality floor's trough test, so "trough" means one thing
    # throughout: deep enough to be a real decline, shallow enough to be cyclical.
    if growth_pct is None or gm_delta_pp is None:
        return 0.0
    if not (TROUGH_MIN_REVENUE_GROWTH_PCT <= growth_pct
            <= TROUGH_MAX_REVENUE_GROWTH_PCT):
        return 0.0
    decline = abs(growth_pct)
    if decline < 1e-9:
        return 0.0
    if gm_delta_pp >= 0:
        return 100.0  # margin expanded while revenue fell
    ratio = abs(gm_delta_pp) / decline
    return round(max(0.0, 100.0 - (ratio / TROUGH_GIVEUP_RATIO_ZERO) * 100.0), 1)


def _score_revenue_growth(
    growth_pct: float | None, gm_delta_pp: float | None = None
) -> float:
    """Higher growth -> higher score. >50% = 100.

    A decline scores zero unless the gross margin held through it, in which
    case it earns partial credit scaled by how much pricing power survived.
    Without that, the component cannot tell a cyclical trough from a business
    in terminal decline — it hands both a zero.
    """
    if growth_pct is None:
        return 0.0
    if growth_pct >= 50:
        return 100.0
    if growth_pct > 0:
        return round((growth_pct / 50) * 100, 1)
    resilience = score_margin_resilience(growth_pct, gm_delta_pp)
    return round(resilience / 100.0 * TROUGH_GROWTH_CREDIT_MAX, 1)


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

    Optional: gross_margin_delta_yoy_pp, which lets the growth component credit
    a decline that held its margin. Absent, a decline scores zero as before.
    """
    w = weights or DEFAULT_WEIGHTS

    components = {
        "revenue_growth": _score_revenue_growth(
            metrics.get("revenue_growth_pct"),
            metrics.get("gross_margin_delta_yoy_pp"),
        ),
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
