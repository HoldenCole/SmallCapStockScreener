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

# --- Scoring weights (must sum to 1.0) ---
DEFAULT_WEIGHTS: dict[str, float] = {
    "revenue_growth": 0.30,
    "gross_margin": 0.20,
    "dilution": 0.20,
    "insider_ownership": 0.15,
    "revenue_acceleration": 0.15,
}

# --- Display settings ---
TOP_N_RESULTS: int = 8  # Max stocks to show per tier

# --- Cache settings ---
CACHE_DIR: str = os.path.join(os.path.dirname(__file__), "data", "cache")
CACHE_TTL_SECONDS: int = 86400  # 24 hours

# --- FMP rate limit delay between calls (seconds) ---
API_RATE_LIMIT_DELAY: float = 0.2
