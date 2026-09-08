"""Four-quadrant macro regime classifier.

Ported from the IBKRTradingBot repo (`src/regime/quadrant.py`, branch
claude/trading-bot-cl1-uso-spread-02qa7h) so the screener is tested against the
user's already-validated classifier rather than an ad-hoc one. The definition
there is explicitly locked — "no per-strategy tuning of the SMA length or
proxies" — so this is a faithful port, not an adaptation:

    growth ON    = SPY month-end close > its 10-month SMA
    inflation ON = DBC month-end close > its 10-month SMA
    month T uses the classification computed through the end of month T-1
    fail closed: insufficient history -> unclassified -> no entry

Reference results there (2007-2026, next-month annualised): GROWTH pays
equities (QQQ +22.3%), REFLATION pays commodities, STAGFLATION pays nothing but
bonds, and DEFLATION hides violent post-crash equity rebounds the lagging
classifier has not caught up to.

This module reuses the local Bars type instead of pandas so the backtest keeps
one price representation throughout.
"""

from __future__ import annotations

import datetime as dt
from enum import Enum

from .prices import Bars

SMA_MONTHS = 10


class Quadrant(Enum):
    GROWTH = "G+I-"
    REFLATION = "G+I+"
    STAGFLATION = "G-I+"
    DEFLATION = "G-I-"


def month_end_closes(bars: Bars) -> list[tuple[dt.date, float]]:
    """Last available close of each calendar month, oldest first."""
    out: dict[tuple[int, int], tuple[dt.date, float]] = {}
    for d, c in zip(bars.dates, bars.close):
        out[(d.year, d.month)] = (d, c)
    return [out[k] for k in sorted(out)]


def _on(series: list[float]) -> bool | None:
    """Is the latest value above the trailing SMA_MONTHS mean?"""
    if len(series) < SMA_MONTHS:
        return None
    return series[-1] > sum(series[-SMA_MONTHS:]) / SMA_MONTHS


def classify(spy_months: list[float], dbc_months: list[float]) -> Quadrant | None:
    growth_on = _on(spy_months)
    infl_on = _on(dbc_months)
    if growth_on is None or infl_on is None:
        return None
    if growth_on and not infl_on:
        return Quadrant.GROWTH
    if growth_on and infl_on:
        return Quadrant.REFLATION
    if infl_on:
        return Quadrant.STAGFLATION
    return Quadrant.DEFLATION


def quadrant_by_month(spy: Bars, dbc: Bars) -> dict[tuple[int, int], Quadrant]:
    """Map (year, month) -> quadrant IN FORCE for that month.

    The value stored against month T is computed from closes through the end of
    month T-1, so a caller looking up an entry date never sees data from the
    month it is trading in.
    """
    spy_me = month_end_closes(spy)
    dbc_me = month_end_closes(dbc)
    dbc_by_month = {(d.year, d.month): c for d, c in dbc_me}

    out: dict[tuple[int, int], Quadrant] = {}
    spy_vals: list[float] = []
    dbc_vals: list[float] = []
    for d, c in spy_me:
        key = (d.year, d.month)
        if key not in dbc_by_month:
            continue
        spy_vals.append(c)
        dbc_vals.append(dbc_by_month[key])
        q = classify(spy_vals, dbc_vals)
        if q is None:
            continue
        # Applies to the FOLLOWING month — this is the ex-ante shift.
        nxt = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
        out[nxt] = q
    return out


def quadrant_on(qmap: dict[tuple[int, int], Quadrant],
                day: dt.date) -> Quadrant | None:
    return qmap.get((day.year, day.month))
