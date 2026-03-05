"""Formatting helpers for the Streamlit UI."""

from typing import Any

import pandas as pd


def fmt_market_cap(cap_m: float | None) -> str:
    """Format market cap in millions to a readable string."""
    if cap_m is None:
        return "N/A"
    if cap_m >= 1000:
        return f"${cap_m / 1000:.1f}B"
    return f"${cap_m:.0f}M"


def fmt_pct(value: float | None, decimals: int = 1) -> str:
    if value is None:
        return "N/A"
    return f"{value:.{decimals}f}%"


def fmt_score(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.0f}"


def compute_revenue_metrics(
    income_stmts: list[dict[str, Any]],
) -> dict[str, Any]:
    """Derive revenue growth and acceleration from quarterly income statements.

    Expects statements sorted most-recent-first (FMP default).
    Returns dict with revenue_growth_pct, revenue_acceleration_pct,
    gross_margin_pct, and quarterly series data.
    """
    result: dict[str, Any] = {
        "revenue_growth_pct": None,
        "revenue_acceleration_pct": None,
        "gross_margin_pct": None,
        "quarterly_revenue": [],
        "quarterly_gross_margin": [],
        "quarterly_dates": [],
    }

    if not income_stmts or len(income_stmts) < 5:
        # Need at least 5 quarters (current + 4 prior) for YoY growth
        if income_stmts:
            latest = income_stmts[0]
            rev = latest.get("revenue", 0)
            gp = latest.get("grossProfit", 0)
            if rev and rev > 0:
                result["gross_margin_pct"] = round(gp / rev * 100, 1)
        return result

    # Build quarterly series (chronological order for charts)
    for stmt in reversed(income_stmts):
        result["quarterly_revenue"].append(stmt.get("revenue", 0))
        rev = stmt.get("revenue", 0)
        gp = stmt.get("grossProfit", 0)
        gm = round(gp / rev * 100, 1) if rev and rev > 0 else 0.0
        result["quarterly_gross_margin"].append(gm)
        result["quarterly_dates"].append(stmt.get("date", ""))

    # Latest gross margin
    latest = income_stmts[0]
    rev_now = latest.get("revenue", 0)
    gp_now = latest.get("grossProfit", 0)
    if rev_now and rev_now > 0:
        result["gross_margin_pct"] = round(gp_now / rev_now * 100, 1)

    # YoY revenue growth (latest Q vs same Q one year ago)
    rev_yoy = income_stmts[4].get("revenue", 0) if len(income_stmts) > 4 else 0
    if rev_yoy and rev_yoy > 0:
        result["revenue_growth_pct"] = round((rev_now - rev_yoy) / rev_yoy * 100, 1)

    # Revenue acceleration: compare recent YoY growth vs prior YoY growth
    if len(income_stmts) >= 8:
        rev_q1_prior = income_stmts[4].get("revenue", 0)
        rev_q1_2yr = income_stmts[7].get("revenue", 0) if len(income_stmts) > 7 else 0
        if rev_q1_2yr and rev_q1_2yr > 0 and rev_yoy and rev_yoy > 0:
            prior_growth = (rev_q1_prior - rev_q1_2yr) / rev_q1_2yr * 100
            current_growth = result["revenue_growth_pct"] or 0
            result["revenue_acceleration_pct"] = round(
                current_growth - prior_growth, 1
            )

    return result


def compute_dilution(
    enterprise_values: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute 3-year share dilution from enterprise value data.

    Returns dict with dilution_3yr_pct, and shares_outstanding list.
    """
    result: dict[str, Any] = {
        "dilution_3yr_pct": None,
        "shares_outstanding": [],
        "shares_dates": [],
    }

    if not enterprise_values:
        return result

    # Chronological order
    sorted_ev = sorted(enterprise_values, key=lambda x: x.get("date", ""))

    for ev in sorted_ev:
        shares = ev.get("numberOfShares")
        if shares:
            result["shares_outstanding"].append(shares)
            result["shares_dates"].append(ev.get("date", ""))

    if len(result["shares_outstanding"]) >= 2:
        oldest = result["shares_outstanding"][0]
        newest = result["shares_outstanding"][-1]
        if oldest and oldest > 0:
            result["dilution_3yr_pct"] = round(
                (newest - oldest) / oldest * 100, 1
            )

    return result


def safe_get(d: dict, *keys: str, default: Any = None) -> Any:
    """Nested dict access that never throws."""
    current = d
    for k in keys:
        if isinstance(current, dict):
            current = current.get(k, default)
        else:
            return default
    return current
