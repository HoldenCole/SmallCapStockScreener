"""Configuration: API keys, industry whitelists, scoring weights, and thresholds."""

import os
from dotenv import load_dotenv

load_dotenv()

FMP_API_KEY: str = os.getenv("FMP_API_KEY", "")

# --- Market cap tiers (in millions USD) ---
TIERS: dict[str, dict[str, float]] = {
    "Nano Cap ($50M–$300M)": {"min": 50, "max": 300},
    "Small Cap ($300M–$2B)": {"min": 300, "max": 2000},
    "Breakout ($2B–$15B)": {"min": 2000, "max": 15000},
    # The entrenched vital-link tier: the dominant supplier of a component a
    # secular chain physically requires, bought at the trough of its own cycle
    # while the end market inflects. Corning at end-2023 was $26B and would
    # have been rejected by every tier below this. Capped at $100B because past
    # that the re-rating this screener looks for is already priced.
    "Vital Link ($15B–$100B)": {"min": 15000, "max": 100000},
}

# --- Industry whitelist (FMP sector/industry strings) ---
# Precise industries: pass on industry name alone
INDUSTRY_WHITELIST: list[str] = [
    "Aerospace & Defense",
    "Electronic Components",
    "Semiconductors",
    "Semiconductor Equipment & Materials",
    "Communication Equipment",
    "Scientific & Technical Instruments",
    "Security & Protection Services",
    "Solar",
    "Electrical Equipment & Parts",
]

# Broad industries: only pass if description ALSO matches target keywords
# (prevents prepaid card companies, document management, generic IT, etc.)
INDUSTRY_WHITELIST_BROAD: list[str] = [
    "Software - Infrastructure",
    "Software - Application",
    "Information Technology Services",
    "Specialty Industrial Machinery",
    "Specialty Chemicals",
]

# --- Keyword fallback for company descriptions ---
# Multi-word phrases: safe to substring-match
DESCRIPTION_KEYWORDS: list[str] = [
    # Aerospace & space
    "satellite", "space launch", "space system", "rocket propulsion",
    "hypersonic", "defense technology", "electronic warfare",
    # Quantum & photonics
    "quantum computing", "photonics", "laser system", "lidar",
    "fiber optic", "optical network", "optical interconnect",
    # AI & compute
    "artificial intelligence", "machine learning", "deep learning",
    "data center infrastructure", "edge computing",
    "autonomous vehicle", "autonomous driving",
    # Semiconductors & chokepoint
    "semiconductor", "sensor fusion", "wireless infrastructure",
    # Defense & security
    "weapons detection", "security screening", "missile defense",
    "munitions", "defense electronics",
    # Reshoring & domestic mfg
    "domestic manufacturing", "reshoring",
    # New energy & batteries
    "battery technology", "energy storage", "lithium ion", "lithium-ion",
    "solid state battery", "solid-state battery",
    "solar cell", "solar panel", "solar energy", "solar module",
    "renewable energy", "wind turbine", "hydrogen fuel", "fuel cell",
    "electric vehicle", "power electronics", "grid scale",
    # Gene editing
    "gene editing", "gene therapy", "genome editing", "genome engineering",
    "CRISPR", "base editing", "genomic medicine",
]
# Short/ambiguous terms: require word-boundary matching (regex \b)
# Bare "laser" removed — too many false positives (laser cutting in
# machinery companies). Real laser companies match via multi-word
# phrases or precise whitelist industries.
DESCRIPTION_KEYWORDS_STRICT: list[str] = [
    "drone", "radar", "photon", "optical",
    "quantum", "rocket", "lidar",
    "EV charging", "inverter",
]

# --- Sectors worth checking descriptions for keyword fallback ---
# These don't match our industry whitelist but may contain relevant companies
DESCRIPTION_CHECK_SECTORS: list[str] = [
    "Technology",
    "Industrials",
    "Communication Services",
    "Healthcare",
    "Basic Materials",
    "Energy",
]

# --- Sectors/industries to exclude unconditionally ---
EXCLUDED_SECTORS: list[str] = [
    # Biotech & pharma (gene editing rescued via description keywords)
    "Biotechnology",
    "Pharmaceuticals",
    "Drug Manufacturers",
    "Drug Manufacturers - General",
    "Drug Manufacturers - Specialty & Generic",
    "Pharmaceutical Retailers",
    # Oil & gas — not structural growth
    "Oil & Gas Equipment & Services",
    "Oil & Gas E&P",
    "Oil & Gas Integrated",
    "Oil & Gas Midstream",
    "Oil & Gas Refining & Marketing",
]

# --- Position sizing by tier ---
# Risk multiplier applied to the conviction-based base size. Smaller, less
# liquid names get less; the entrenched vital-link tier gets more, since a
# $15B+ incumbent bought at a cyclical trough carries far less single-name
# risk than a $100M nano cap. Matched by substring against the tier label.
TIER_POSITION_MULTIPLIER: dict[str, float] = {
    "Nano": 0.6,
    "Small": 1.0,
    "Breakout": 1.2,
    "Vital Link": 1.4,
}


def tier_position_multiplier(tier_name: str) -> float:
    """Position-size multiplier for a tier label, defaulting to 1.0."""
    for key, mult in TIER_POSITION_MULTIPLIER.items():
        if key in tier_name:
            return mult
    return 1.0


# --- Sanity bounds ---
# A gross margin at or above this is missing cost-of-revenue data rather than a
# real margin; no operating business has literally zero cost of revenue.
SANITY_MAX_GROSS_MARGIN_PCT: float = 99.5

# --- Quality floor ---
# Normal path: a business must actually be growing.
QUALITY_MIN_REVENUE_GROWTH_PCT: float = 5.0
QUALITY_MIN_GROSS_MARGIN_PCT: float = 20.0
QUALITY_MAX_DILUTION_3YR_PCT: float = 30.0

# Trough exception. A revenue decline is acceptable when gross margin holds
# through it — an incumbent nobody can design around keeps its price when
# volume falls, while a company losing its position discounts to hold share.
# This is the only way the vital-link archetype is reachable: Corning at its
# end-2023 entry had revenue down 11.3% with gross margin down 0.6pp, and the
# plain >5% growth rule rejected it outright.
TROUGH_MIN_REVENUE_GROWTH_PCT: float = -25.0   # deeper than this is decline
TROUGH_MAX_REVENUE_GROWTH_PCT: float = -5.0    # shallower than this is not a trough
TROUGH_MAX_GM_GIVEUP_PP: float = 2.0           # margin may give up this much
TROUGH_MIN_GROSS_MARGIN_PCT: float = 25.0      # and must still be a real margin

# Trough credit in the composite. The growth component returns zero for any
# decline, which scores an incumbent holding its price through a cyclical low
# identically to a business in terminal decline. A margin-holding trough earns
# partial credit instead, measured as margin given up per point of revenue
# decline — Corning conceded 0.6pp on an 11.3% fall, a ratio of 0.05.
#
# Capped below what real growth earns: keeping your price through a downturn is
# evidence of pricing power, but growing is still better.
TROUGH_GROWTH_CREDIT_MAX: float = 50.0   # best a trough can score, = a 25% grower
TROUGH_GIVEUP_RATIO_ZERO: float = 0.5    # pp conceded per % of decline -> no credit

# --- Combined score blend (must sum to 1.0) ---
# Set from an 18-date point-in-time reconstruction, 2,653 scored evaluations
# with 12-month forward returns and every statement admitted on its filing
# date. Each component was measured the same way — quartiles ranked within
# each date, then checked for per-date consistency and against a market-cap
# control:
#
#   component     Q1 median   Q4 median   dates correct   size-controlled
#   composite         +2.8%       +4.4%          11/18     -8.0 / -9.6 pp
#   fingerprint       +7.5%       -3.4%          14/18     +8.0 / +6.5 pp
#   WPS               +4.4%       +1.9%           8/18     -0.8 / -6.6 pp
#
# Composite goes to zero. It ranks no better than chance, and its spread is
# NEGATIVE once size is controlled — a 0.35 weight on that was worse than no
# weight at all. This does not make its inputs useless: the fingerprint scores
# the same raw metrics, but against reference archetypes rather than absolute
# curves, and the quality floor still uses them.
#
# WPS keeps a minority weight despite failing the median and consistency
# tests, because it does something the fingerprint does not: its top quartile
# hit +50% returns 25% of the time against 14% for its bottom quartile, and
# carried a +38.2% mean against +11.0%. That is tail capture, which is the
# actual objective, even though it is not a "which name is better" signal.
#
# Deliberately not set to 100% fingerprint, which scored best on this sample.
# The sweep was run on the same data these weights would be fitted to, so
# taking its argmax is overfitting; a single-component score is also fragile
# to that one component changing when the reference set does.
COMBINED_WEIGHTS: dict[str, float] = {
    "composite": 0.00,
    "fingerprint": 0.75,
    "wps": 0.25,
}

# --- Composite internal weights (must sum to 1.0) ---
DEFAULT_WEIGHTS: dict[str, float] = {
    "revenue_growth": 0.30,
    "gross_margin": 0.20,
    "dilution": 0.20,
    "insider_ownership": 0.15,
    "revenue_acceleration": 0.15,
}

# --- Display settings ---
TOP_N_RESULTS: int = 10  # Max stocks to show per tier

# --- Cache settings ---
CACHE_DIR: str = os.path.join(os.path.dirname(__file__), "data", "cache")
CACHE_TTL_SECONDS: int = 86400  # 24 hours

# --- FMP rate limit delay between calls (seconds) ---
API_RATE_LIMIT_DELAY: float = 0.2
