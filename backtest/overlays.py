"""Entry overlays: market regime and momentum.

Both are lifted from the IBKR trading bot spec. They are explicitly NOT the
screener's strategy — they are tested as a gate layered on top of whatever the
screen produces, to see whether they add anything.
"""

from __future__ import annotations

import datetime as dt

from .prices import Bars


def regime_active(bench: Bars, day: dt.date) -> bool:
    """SPY 50/200 regime, Variant 1 definition from the commodity spec.

    ON when close > SMA(50) AND SMA(50) > SMA(200).

    Fails closed: if there is not enough history, or the date falls outside the
    series, the regime is OFF and no entry is taken. That matches the bot spec's
    "if the regime service is unreachable, treat as OFF".
    """
    i = bench.index_on_or_after(day)
    if i is None:
        return False
    sma50 = bench.sma(i, 50)
    sma200 = bench.sma(i, 200)
    if sma50 is None or sma200 is None:
        return False
    return bench.close[i] > sma50 and sma50 > sma200


def momentum_12_1(bars: Bars, day: dt.date) -> float | None:
    """12-month return skipping the most recent month (classic 12-1 momentum).

    The one-month skip avoids the well-documented short-term reversal effect
    that contaminates a raw 12-month lookback.
    """
    i = bars.index_on_or_after(day)
    if i is None or i < 252:
        return None
    start = bars.close[i - 252]
    end = bars.close[i - 21]
    if start <= 0:
        return None
    return (end - start) / start * 100


def vol_adjusted_momentum(bars: Bars, day: dt.date) -> float | None:
    """12-month return divided by 12-month realised vol (Variant 3 signal)."""
    i = bars.index_on_or_after(day)
    if i is None:
        return None
    mom = momentum_12_1(bars, day)
    vol = bars.realized_vol(i, 252)
    if mom is None or vol is None or vol <= 0:
        return None
    return mom / (vol * 100)
