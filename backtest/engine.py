"""Position simulation and result aggregation."""

from __future__ import annotations

import datetime as dt
import statistics as st
from dataclasses import dataclass

from .exits import MAX_HOLD_BARS, ExitRule, PositionState
from .prices import Bars


@dataclass
class Trade:
    ticker: str
    entry_date: dt.date
    exit_date: dt.date
    ret_pct: float
    bars_held: int
    max_gain_pct: float
    max_drawdown_pct: float
    reasons: str


def simulate(bars: Bars, entry_day: dt.date, rule: ExitRule) -> Trade | None:
    """Run one position from `entry_day` under `rule`.

    Returns None when there is no tradable bar on or after the entry date, or
    when fewer than two bars remain (nothing to hold).
    """
    ei = bars.index_on_or_after(entry_day)
    if ei is None or ei >= len(bars) - 1:
        return None

    entry_price = bars.close[ei]
    if entry_price <= 0:
        return None

    state = PositionState(entry_idx=ei, entry_price=entry_price,
                          peak_close=entry_price)
    proceeds = 0.0
    peak = entry_price
    trough_from_peak = 0.0
    max_gain = 0.0

    last = min(ei + MAX_HOLD_BARS, len(bars) - 1)
    exit_idx = last

    for i in range(ei + 1, last + 1):
        c = bars.close[i]
        peak = max(peak, c)
        max_gain = max(max_gain, (peak - entry_price) / entry_price * 100)
        trough_from_peak = min(trough_from_peak, (c - peak) / peak * 100)

        for frac, reason in rule(bars, i, state):
            frac = min(frac, state.remaining)
            if frac <= 0:
                continue
            proceeds += frac * c
            state.remaining -= frac
            state.realized.append((frac, c, reason))

        if state.remaining <= 1e-9:
            exit_idx = i
            break

    # Anything still open at the horizon is marked out at the final close.
    if state.remaining > 1e-9:
        proceeds += state.remaining * bars.close[exit_idx]
        state.realized.append((state.remaining, bars.close[exit_idx], "horizon"))

    return Trade(
        ticker=bars.ticker,
        entry_date=bars.dates[ei],
        exit_date=bars.dates[exit_idx],
        ret_pct=(proceeds - entry_price) / entry_price * 100,
        bars_held=exit_idx - ei,
        max_gain_pct=max_gain,
        max_drawdown_pct=trough_from_peak,
        reasons=",".join(sorted({r for _, _, r in state.realized})),
    )


@dataclass
class RuleResult:
    name: str
    n: int
    mean: float
    median: float
    win_rate: float
    p25: float
    p75: float
    mean_hold: float
    worst: float
    best: float
    mean_maxdd: float
    ret_over_dd: float

    def row(self) -> str:
        return (f"{self.name:<18}{self.n:>6}{self.mean:>8.1f}{self.median:>8.1f}"
                f"{self.win_rate:>7.0f}%{self.p25:>8.1f}{self.p75:>8.1f}"
                f"{self.mean_hold:>7.0f}{self.worst:>8.0f}{self.ret_over_dd:>8.2f}")

    @staticmethod
    def header() -> str:
        return (f"{'rule':<18}{'n':>6}{'mean':>8}{'med':>8}{'win':>8}"
                f"{'p25':>8}{'p75':>8}{'hold':>7}{'worst':>8}{'ret/dd':>8}")


def summarize(name: str, trades: list[Trade]) -> RuleResult | None:
    if not trades:
        return None
    r = sorted(t.ret_pct for t in trades)
    n = len(r)
    dd = [abs(t.max_drawdown_pct) for t in trades]
    mean_dd = st.mean(dd) if dd else 0.0
    med = st.median(r)
    return RuleResult(
        name=name, n=n, mean=st.mean(r), median=med,
        win_rate=100.0 * sum(1 for x in r if x > 0) / n,
        p25=r[max(0, int(0.25 * n) - 1)], p75=r[min(n - 1, int(0.75 * n))],
        mean_hold=st.mean([t.bars_held for t in trades]),
        worst=r[0], best=r[-1], mean_maxdd=mean_dd,
        ret_over_dd=(med / mean_dd) if mean_dd > 0 else 0.0,
    )
