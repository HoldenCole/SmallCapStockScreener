"""Daily bar data for backtesting.

Deliberately separate from FMPClient: this is price history for backtest
research, not screener input, and it should never consume FMP rate limits that
the screener needs. Bars are cached to disk indefinitely — historical closes do
not change, so there is no TTL here.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import time
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

CACHE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "cache", "prices",
)
_UA = "Mozilla/5.0 (compatible; StockScreener-backtest/1.0)"


class Bars:
    """Daily OHLC series for one ticker, indexed by date."""

    def __init__(self, ticker: str, dates: list[dt.date],
                 close: list[float], high: list[float], low: list[float]) -> None:
        self.ticker = ticker
        self.dates = dates
        self.close = close
        self.high = high
        self.low = low
        self._idx = {d: i for i, d in enumerate(dates)}

    def __len__(self) -> int:
        return len(self.dates)

    def index_on_or_after(self, day: dt.date) -> int | None:
        """First bar index on or after `day`, or None if the series ends first."""
        i = self._idx.get(day)
        if i is not None:
            return i
        for j, d in enumerate(self.dates):
            if d >= day:
                return j
        return None

    def sma(self, i: int, window: int) -> float | None:
        if i + 1 < window:
            return None
        return sum(self.close[i + 1 - window: i + 1]) / window

    def atr(self, i: int, window: int = 20) -> float | None:
        """Average true range over `window` bars ending at i."""
        if i + 1 < window + 1:
            return None
        trs = []
        for k in range(i + 1 - window, i + 1):
            prev_close = self.close[k - 1]
            trs.append(max(
                self.high[k] - self.low[k],
                abs(self.high[k] - prev_close),
                abs(self.low[k] - prev_close),
            ))
        return sum(trs) / len(trs)

    def realized_vol(self, i: int, window: int) -> float | None:
        """Annualised stdev of daily log returns over `window` bars."""
        import math
        if i + 1 < window + 1:
            return None
        rets = []
        for k in range(i + 1 - window, i + 1):
            if self.close[k - 1] > 0:
                rets.append(math.log(self.close[k] / self.close[k - 1]))
        if len(rets) < 2:
            return None
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
        return (var ** 0.5) * (252 ** 0.5)


def _fetch(ticker: str, rng: str = "5y") -> dict[str, Any] | None:
    url = (f"https://query2.finance.yahoo.com/v8/finance/chart/{ticker}"
           f"?range={rng}&interval=1d")
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.load(resp)
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            wait = 2 ** attempt
            logger.warning("price fetch failed for %s (%s), retry in %ds",
                           ticker, exc, wait)
            time.sleep(wait)
    logger.error("giving up on price fetch for %s", ticker)
    return None


def load(ticker: str, rng: str = "5y", use_cache: bool = True) -> Bars | None:
    """Load daily bars, from disk cache when available."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, f"{ticker}_{rng}.json")

    raw: dict[str, Any] | None = None
    if use_cache and os.path.exists(path):
        try:
            with open(path) as f:
                raw = json.load(f)
        except (OSError, ValueError):
            raw = None

    if raw is None:
        raw = _fetch(ticker, rng)
        if raw is None:
            return None
        try:
            with open(path, "w") as f:
                json.dump(raw, f)
        except OSError as exc:
            logger.warning("could not cache %s: %s", ticker, exc)

    try:
        res = raw["chart"]["result"][0]
        ts = res["timestamp"]
        q = res["indicators"]["quote"][0]
        dates, close, high, low = [], [], [], []
        for t, c, h, lo in zip(ts, q["close"], q["high"], q["low"]):
            if c is None:
                continue
            dates.append(dt.datetime.utcfromtimestamp(t).date())
            close.append(float(c))
            # High/low occasionally come back null even when close is present.
            high.append(float(h) if h is not None else float(c))
            low.append(float(lo) if lo is not None else float(c))
        if len(dates) < 2:
            return None
        return Bars(ticker, dates, close, high, low)
    except (KeyError, IndexError, TypeError) as exc:
        logger.error("malformed price payload for %s: %s", ticker, exc)
        return None


def load_many(tickers: list[str], rng: str = "5y",
              pause: float = 0.4) -> dict[str, Bars]:
    """Load bars for many tickers, skipping any that fail."""
    out: dict[str, Bars] = {}
    for t in tickers:
        cached = os.path.exists(os.path.join(CACHE_DIR, f"{t}_{rng}.json"))
        b = load(t, rng)
        if b is not None:
            out[t] = b
        if not cached:
            time.sleep(pause)  # only rate-limit actual network calls
    return out
