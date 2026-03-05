"""FMP API client with caching and rate limiting.

Uses the /stable/ endpoint format (FMP migrated from legacy /v3/ and /v4/).
"""

import hashlib
import json
import logging
import os
import time
from typing import Any

import requests

from config import API_RATE_LIMIT_DELAY, CACHE_DIR, CACHE_TTL_SECONDS

logger = logging.getLogger(__name__)


class FMPClient:
    """Wrapper around the Financial Modeling Prep API (stable endpoints)."""

    BASE_URL = "https://financialmodelingprep.com/stable"

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self._last_call: float = 0.0
        os.makedirs(CACHE_DIR, exist_ok=True)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _rate_limit(self) -> None:
        elapsed = time.time() - self._last_call
        if elapsed < API_RATE_LIMIT_DELAY:
            time.sleep(API_RATE_LIMIT_DELAY - elapsed)
        self._last_call = time.time()

    def _cache_key(self, url: str, params: dict[str, Any]) -> str:
        raw = url + json.dumps(params, sort_keys=True)
        return hashlib.md5(raw.encode()).hexdigest()

    def _read_cache(self, key: str) -> Any | None:
        path = os.path.join(CACHE_DIR, f"{key}.json")
        if not os.path.exists(path):
            return None
        age = time.time() - os.path.getmtime(path)
        if age > CACHE_TTL_SECONDS:
            os.remove(path)
            return None
        with open(path) as f:
            return json.load(f)

    def _write_cache(self, key: str, data: Any) -> None:
        path = os.path.join(CACHE_DIR, f"{key}.json")
        with open(path, "w") as f:
            json.dump(data, f)

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """Make a GET request with caching and rate limiting."""
        params = params or {}
        params["apikey"] = self.api_key
        url = f"{self.BASE_URL}/{path.lstrip('/')}"
        cache_key = self._cache_key(url, params)

        cached = self._read_cache(cache_key)
        if cached is not None:
            return cached

        self._rate_limit()
        try:
            resp = requests.get(url, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            self._write_cache(cache_key, data)
            return data
        except requests.RequestException as exc:
            logger.error("FMP API error for %s: %s", path, exc)
            return []

    # ------------------------------------------------------------------
    # Public API methods
    # ------------------------------------------------------------------

    def screen_stocks(
        self,
        min_cap: float,
        max_cap: float,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """Broad screener query. min_cap/max_cap in millions."""
        result = self._get(
            "company-screener",
            {
                "marketCapMoreThan": int(min_cap * 1_000_000),
                "marketCapLowerThan": int(max_cap * 1_000_000),
                "isEtf": False,
                "isFund": False,
                "country": "US",
                "isActivelyTrading": True,
                "limit": limit,
            },
        )
        if isinstance(result, list):
            return result
        return []

    def get_profile(self, ticker: str) -> dict[str, Any]:
        """Company profile: description, sector, industry, market cap."""
        result = self._get("profile", {"symbol": ticker})
        if isinstance(result, list) and result:
            return result[0]
        return {}

    def get_income_statements(
        self, ticker: str, quarters: int = 8
    ) -> list[dict[str, Any]]:
        """Quarterly income statements (most recent first)."""
        result = self._get(
            "income-statement",
            {"symbol": ticker, "period": "quarter", "limit": quarters},
        )
        if isinstance(result, list):
            return result
        return []

    def get_enterprise_values(
        self, ticker: str, quarters: int = 12
    ) -> list[dict[str, Any]]:
        """Enterprise values including share counts over time."""
        result = self._get(
            "enterprise-values",
            {"symbol": ticker, "period": "quarter", "limit": quarters},
        )
        if isinstance(result, list):
            return result
        return []

    def get_key_metrics_ttm(self, ticker: str) -> dict[str, Any]:
        """Trailing twelve month key metrics."""
        result = self._get("key-metrics-ttm", {"symbol": ticker})
        if isinstance(result, list) and result:
            return result[0]
        return {}

    def get_ratios_ttm(self, ticker: str) -> dict[str, Any]:
        """Trailing twelve month financial ratios."""
        result = self._get("ratios-ttm", {"symbol": ticker})
        if isinstance(result, list) and result:
            return result[0]
        return {}
