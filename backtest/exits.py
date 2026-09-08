"""Exit rule library.

Each rule is a callable that, given the bars and the position state so far,
returns a list of (fraction_to_sell, reason) for the current bar. Rules are
pure functions of data available up to and including the current bar — no
lookahead.

Fills are modelled at the close of the bar on which the condition triggers,
never at the trigger price itself. That is deliberately conservative for
targets and deliberately optimistic for stops (see METHODOLOGY.md limitation 4);
modelling stops at the trigger price would assume a fill that a gap-down would
not have given us.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Protocol

from .prices import Bars

MAX_HOLD_BARS = 252


@dataclass
class PositionState:
    """Mutable state carried through one simulated position."""

    entry_idx: int
    entry_price: float
    remaining: float = 1.0
    peak_close: float = 0.0
    trail_level: float | None = None
    realized: list[tuple[float, float, str]] = field(default_factory=list)
    """(fraction, price, reason) for each partial or full sale."""


class ExitRule(Protocol):
    name: str

    def __call__(self, bars: Bars, i: int, st: PositionState
                 ) -> list[tuple[float, str]]:
        ...


def _rule(name: str) -> Callable[[Callable], ExitRule]:
    def deco(fn: Callable) -> ExitRule:
        fn.name = name  # type: ignore[attr-defined]
        return fn  # type: ignore[return-value]
    return deco


# ---------------------------------------------------------------- baselines

def buy_and_hold(hold_bars: int) -> ExitRule:
    @_rule(f"hold_{hold_bars}d")
    def f(bars: Bars, i: int, st: PositionState) -> list[tuple[float, str]]:
        if i - st.entry_idx >= hold_bars:
            return [(st.remaining, "horizon")]
        return []
    return f


# ------------------------------------------------------------------ targets

def profit_target(pct: float) -> ExitRule:
    @_rule(f"target_{pct:+.0f}%")
    def f(bars: Bars, i: int, st: PositionState) -> list[tuple[float, str]]:
        if bars.close[i] >= st.entry_price * (1 + pct / 100):
            return [(st.remaining, "target")]
        return []
    return f


def stop_loss(pct: float) -> ExitRule:
    @_rule(f"stop_{pct:+.0f}%")
    def f(bars: Bars, i: int, st: PositionState) -> list[tuple[float, str]]:
        if bars.close[i] <= st.entry_price * (1 + pct / 100):
            return [(st.remaining, "stop")]
        return []
    return f


def target_and_stop(target_pct: float, stop_pct: float) -> ExitRule:
    @_rule(f"tgt{target_pct:+.0f}/stp{stop_pct:+.0f}")
    def f(bars: Bars, i: int, st: PositionState) -> list[tuple[float, str]]:
        c = bars.close[i]
        # Stop takes precedence over target when both trigger on one bar —
        # risk control wins, matching the IBKR exit-priority ordering.
        if c <= st.entry_price * (1 + stop_pct / 100):
            return [(st.remaining, "stop")]
        if c >= st.entry_price * (1 + target_pct / 100):
            return [(st.remaining, "target")]
        return []
    return f


# ----------------------------------------------------------------- trailing

def trailing_stop(pct: float) -> ExitRule:
    """Percentage trailing stop off the highest close since entry."""

    @_rule(f"trail_{pct:.0f}%")
    def f(bars: Bars, i: int, st: PositionState) -> list[tuple[float, str]]:
        st.peak_close = max(st.peak_close, bars.close[i])
        level = st.peak_close * (1 - pct / 100)
        st.trail_level = max(st.trail_level or 0.0, level)  # ratchets only up
        if bars.close[i] <= st.trail_level and i > st.entry_idx:
            return [(st.remaining, "trail")]
        return []
    return f


def atr_trailing_stop(mult: float, window: int = 20,
                      activate_at_r: float = 1.0,
                      r_pct: float = 10.0) -> ExitRule:
    """ATR trailing stop, per the IBKR runner spec.

    Ratchets upward only, and stays inactive until the position is up
    `activate_at_r` R, where 1R is defined as `r_pct` of entry price. Before
    activation the position is given room to work rather than being stopped out
    on entry-day noise.
    """

    @_rule(f"atr_{mult:g}x")
    def f(bars: Bars, i: int, st: PositionState) -> list[tuple[float, str]]:
        atr = bars.atr(i, window)
        if atr is None:
            return []
        gain_r = (bars.close[i] - st.entry_price) / (st.entry_price * r_pct / 100)
        if gain_r >= activate_at_r:
            level = bars.close[i] - mult * atr
            st.trail_level = max(st.trail_level or 0.0, level)
        if st.trail_level is not None and bars.close[i] <= st.trail_level:
            return [(st.remaining, "atr_trail")]
        return []
    return f


# --------------------------------------------------------------- time stops

def time_stop(days: int) -> ExitRule:
    @_rule(f"time_{days}d")
    def f(bars: Bars, i: int, st: PositionState) -> list[tuple[float, str]]:
        if (bars.dates[i] - bars.dates[st.entry_idx]).days >= days:
            return [(st.remaining, "time")]
        return []
    return f


# -------------------------------------------------------------- scale-outs

def scale_out(t1: float = 25.0, t2: float = 50.0,
              trail_pct: float = 20.0) -> ExitRule:
    """Ladder adapted from the IBKR options scale-out to a plain equity.

    Sells half at +t1%, a quarter at +t2%, and trails the remainder. The option
    version uses +50/+100% because option premium moves several times faster
    than the underlying; those levels would almost never trigger on a stock, so
    the ladder is scaled down rather than copied across.
    """

    @_rule(f"scale_{t1:.0f}/{t2:.0f}/tr{trail_pct:.0f}")
    def f(bars: Bars, i: int, st: PositionState) -> list[tuple[float, str]]:
        c = bars.close[i]
        sells: list[tuple[float, str]] = []
        hit = {r for _, _, r in st.realized}

        if "scale1" not in hit and c >= st.entry_price * (1 + t1 / 100):
            sells.append((0.50, "scale1"))
        if "scale2" not in hit and c >= st.entry_price * (1 + t2 / 100):
            sells.append((0.25, "scale2"))

        # Runner trails only once the first target has been banked.
        if "scale1" in hit or sells:
            st.peak_close = max(st.peak_close, c)
            st.trail_level = max(st.trail_level or 0.0,
                                 st.peak_close * (1 - trail_pct / 100))
            sold_now = sum(s for s, _ in sells)
            if c <= st.trail_level and st.remaining - sold_now > 0:
                sells.append((st.remaining - sold_now, "runner_trail"))
        return sells
    return f


def build_grid() -> list[ExitRule]:
    """The locked exit-rule grid from METHODOLOGY.md. Tested once, as specified."""
    rules: list[ExitRule] = []
    rules += [buy_and_hold(n) for n in (63, 126, 252)]
    rules += [profit_target(p) for p in (15, 20, 25, 30, 40, 50)]
    rules += [stop_loss(p) for p in (-10, -15, -20, -25)]
    rules += [trailing_stop(p) for p in (10, 15, 20, 25)]
    rules += [atr_trailing_stop(m) for m in (1.5, 2.0, 3.0)]
    rules += [time_stop(d) for d in (30, 60, 90, 180)]
    rules += [scale_out()]
    rules += [target_and_stop(t, s)
              for t in (20, 30, 40) for s in (-15, -20)]
    return rules
