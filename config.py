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
INDUSTRY_WHITELIST: list[str] = [
    "Aerospace & Defense",
    "Electronic Components",
    "Semiconductors",
    "Semiconductor Equipment & Materials",
    "Communication Equipment",
    "Software - Infrastructure",
    "Information Technology Services",
    "Scientific & Technical Instruments",
    "Specialty Industrial Machinery",
]

# --- Keyword fallback for company descriptions ---
# Multi-word phrases: safe to substring-match
DESCRIPTION_KEYWORDS: list[str] = [
    "quantum computing", "photonics", "laser system", "lidar",
    "satellite", "space launch", "space system", "rocket propulsion",
    "autonomous vehicle", "autonomous driving",
    "artificial intelligence", "machine learning", "deep learning",
    "fiber optic", "optical network", "optical interconnect",
    "hypersonic", "sensor fusion", "edge computing",
    "data center infrastructure", "wireless infrastructure",
    "semiconductor", "defense technology", "electronic warfare",
]
# Short/ambiguous terms: require word-boundary matching (regex \b)
DESCRIPTION_KEYWORDS_STRICT: list[str] = [
    "drone", "radar", "photon", "laser", "optical",
    "quantum", "rocket", "space",
]

# --- Sectors worth checking descriptions for keyword fallback ---
# These don't match our industry whitelist but may contain relevant companies
DESCRIPTION_CHECK_SECTORS: list[str] = [
    "Technology",
    "Industrials",
    "Communication Services",
]

# --- Sectors to exclude unconditionally ---
EXCLUDED_SECTORS: list[str] = [
    "Biotechnology",
    "Pharmaceuticals",
    "Drug Manufacturers",
    "Drug Manufacturers - General",
    "Drug Manufacturers - Specialty & Generic",
    "Pharmaceutical Retailers",
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
