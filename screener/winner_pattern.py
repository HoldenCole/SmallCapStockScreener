"""Winner-Pattern Score (WPS) — parallel scoring layer.

Encodes the shared traits of four validated winners (ADTN, MRAM, MXL, XMTR)
into a ranked, explainable pattern-match score. Sits alongside existing
composite and fingerprint scores without modifying them.
"""

import math
from typing import Any

CONFIG = {
    "weights": {
        "inflection": 0.25,
        "tailwind": 0.20,
        "margin": 0.20,
        "balance_sheet": 0.20,
        "beaten_down": 0.10,
        "under_followed": 0.05,
    },
    "gate": {
        "require_negative_net_cash": True,
        "require_cash_burn": True,
        "dilution_yoy_threshold": 0.15,
        "wps_cap_if_tripped": 50,
        # A positive-but-thin cash cushion (< this fraction of market cap) while
        # burning cash and diluting heavily is the same trap as negative net
        # cash: the company can't fund the trough without raising again. Without
        # this, story stocks that hold a small cash balance (the BZAI/KULR
        # profile) slip through the gate despite the exact risk it screens for.
        "weak_net_cash_to_mktcap": 0.20,
    },
    "archetype_min_cosine": 0.80,
    "normalization": "percentile",
    "missing_field_subscore": 40,
}

STRUCTURAL_TAGS = {
    "defense": ["defense", "missile", "radar", "munition",
                "aerospace", "military", "warfare"],
    "fiber_bead": ["fiber", "broadband", "BEAD", "optical",
                   "FTTH", "access network"],
    "semis": ["semiconductor", "wafer", "test", "ASIC",
              "chip", "foundry"],
    "ai_infra": ["GPU", "inference", "edge AI", "data center",
                 "accelerator"],
    "security": ["screening", "weapons detection",
                 "surveillance", "authentication"],
    "reshoring": ["domestic manufacturing", "onshore",
                  "reshoring", "Made in USA"],
    "space": ["satellite", "launch", "orbital", "spacecraft"],
}

# Reference archetype vectors — computed from validation run, then frozen.
# Keys: inflection, tailwind, margin, balance_sheet, beaten_down, under_followed
# These are placeholder vectors that get overwritten by compute_reference_vectors().
ARCHETYPE_VECTORS: dict[str, dict[str, float]] = {}


def _safe_get(d: dict, *keys, default=None):
    """Nested safe dict access."""
    val = d
    for k in keys:
        if isinstance(val, dict):
            val = val.get(k, default)
        else:
            return default
    return val


def _percentile_rank(value: float, values: list[float], higher_is_better: bool = True) -> float:
    """Compute percentile rank (0–100) of value within a list."""
    if not values or value is None:
        return CONFIG["missing_field_subscore"]
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    count_below = sum(1 for v in sorted_vals if v < value)
    rank = (count_below / n) * 100
    if not higher_is_better:
        rank = 100 - rank
    return min(100, max(0, round(rank, 1)))


def _cosine_similarity(a: dict[str, float], b: dict[str, float]) -> float:
    """Cosine similarity between two trait vectors."""
    keys = list(CONFIG["weights"].keys())
    va = [a.get(k, 0) for k in keys]
    vb = [b.get(k, 0) for k in keys]
    dot = sum(x * y for x, y in zip(va, vb))
    mag_a = math.sqrt(sum(x * x for x in va))
    mag_b = math.sqrt(sum(x * x for x in vb))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


def _tag_structural_tailwind(description: str, industry: str) -> list[str]:
    """Return list of structural tailwind tags matching this company."""
    text = (description + " " + industry).lower()
    matched = []
    for tag, keywords in STRUCTURAL_TAGS.items():
        for kw in keywords:
            if kw.lower() in text:
                matched.append(tag)
                break
    return matched


def extract_wps_inputs(
    ticker_data: dict,
    income_quarterly: list[dict],
    income_annual: list[dict],
    balance_sheet: list[dict],
    quote: dict,
    price_change: dict,
    profile: dict,
) -> dict:
    """Extract all raw metrics needed for WPS from FMP data.

    Returns a flat dict of metrics, with None for anything missing.
    """
    result: dict[str, Any] = {"ticker": ticker_data.get("ticker", "")}
    flags: list[str] = []

    # ── Revenue metrics ──
    # YoY revenue growth from annual statements
    if len(income_annual) >= 2:
        rev_curr = income_annual[0].get("revenue", 0) or 0
        rev_prev = income_annual[1].get("revenue", 0) or 0
        if rev_prev > 0:
            result["revenue_growth_yoy"] = (rev_curr - rev_prev) / abs(rev_prev) * 100
        else:
            result["revenue_growth_yoy"] = None
            flags.append("DATA_INCOMPLETE")

        # Was there a prior decline? (check 3yr if available)
        if len(income_annual) >= 3:
            rev_2yr = income_annual[2].get("revenue", 0) or 0
            result["had_prior_decline"] = rev_prev < rev_2yr if rev_2yr > 0 else False
        else:
            result["had_prior_decline"] = False
    else:
        result["revenue_growth_yoy"] = None
        result["had_prior_decline"] = False
        flags.append("DATA_INCOMPLETE")

    # QoQ revenue acceleration from quarterly
    if len(income_quarterly) >= 3:
        q0 = income_quarterly[0].get("revenue", 0) or 0
        q1 = income_quarterly[1].get("revenue", 0) or 0
        q2 = income_quarterly[2].get("revenue", 0) or 0
        if q1 > 0 and q2 > 0:
            growth_recent = (q0 - q1) / abs(q1) * 100
            growth_prior = (q1 - q2) / abs(q2) * 100
            result["revenue_acceleration"] = growth_recent - growth_prior
        else:
            result["revenue_acceleration"] = None
    else:
        result["revenue_acceleration"] = None

    # ── Margin metrics ──
    if len(income_annual) >= 2:
        gp_curr = income_annual[0].get("grossProfit", 0) or 0
        gp_prev = income_annual[1].get("grossProfit", 0) or 0
        rev_curr = income_annual[0].get("revenue", 0) or 0
        rev_prev = income_annual[1].get("revenue", 0) or 0

        gm_curr = (gp_curr / rev_curr * 100) if rev_curr > 0 else None
        gm_prev = (gp_prev / rev_prev * 100) if rev_prev > 0 else None
        result["gross_margin_current"] = gm_curr
        if gm_curr is not None and gm_prev is not None:
            result["gross_margin_delta_yoy"] = gm_curr - gm_prev
        else:
            result["gross_margin_delta_yoy"] = None

        # Operating margin
        oi_curr = income_annual[0].get("operatingIncome", 0) or 0
        oi_prev = income_annual[1].get("operatingIncome", 0) or 0
        om_curr = (oi_curr / rev_curr * 100) if rev_curr > 0 else None
        om_prev = (oi_prev / rev_prev * 100) if rev_prev > 0 else None
        result["op_margin_current"] = om_curr
        if om_curr is not None and om_prev is not None:
            result["op_margin_delta_yoy"] = om_curr - om_prev
        else:
            result["op_margin_delta_yoy"] = None

        # Incremental margin: delta_op_income / delta_revenue
        if rev_curr != rev_prev and rev_curr > 0:
            result["incremental_margin"] = (oi_curr - oi_prev) / abs(rev_curr - rev_prev) * 100 if (rev_curr - rev_prev) != 0 else None
        else:
            result["incremental_margin"] = None

        # Net margin crossing positive
        ni_curr = income_annual[0].get("netIncome", 0) or 0
        ni_prev = income_annual[1].get("netIncome", 0) or 0
        result["net_margin_crossing_positive"] = ni_curr > 0 and ni_prev <= 0
    else:
        for k in ["gross_margin_current", "gross_margin_delta_yoy",
                   "op_margin_current", "op_margin_delta_yoy",
                   "incremental_margin", "net_margin_crossing_positive"]:
            result[k] = None

    # ── Balance sheet metrics ──
    if balance_sheet:
        bs = balance_sheet[0]
        cash = bs.get("cashAndCashEquivalents", 0) or 0
        debt = bs.get("totalDebt", 0) or 0
        result["net_cash"] = cash - debt
        mkt_cap = quote.get("marketCap", 0) or 0
        result["net_cash_to_mkt_cap"] = (result["net_cash"] / mkt_cap) if mkt_cap > 0 else None

        current_assets = bs.get("totalCurrentAssets", 0) or 0
        current_liab = bs.get("totalCurrentLiabilities", 0) or 0
        result["current_ratio"] = (current_assets / current_liab) if current_liab > 0 else None

        # Shares outstanding YoY change
        shares_curr = bs.get("commonStock", 0) or bs.get("weightedAverageShsOut", 0) or 0
        if len(balance_sheet) >= 4:
            shares_prev = balance_sheet[3].get("commonStock", 0) or balance_sheet[3].get("weightedAverageShsOut", 0) or 0
        elif len(balance_sheet) >= 2:
            shares_prev = balance_sheet[-1].get("commonStock", 0) or balance_sheet[-1].get("weightedAverageShsOut", 0) or 0
        else:
            shares_prev = shares_curr
        if shares_prev > 0:
            result["share_count_delta_yoy"] = (shares_curr - shares_prev) / shares_prev
        else:
            result["share_count_delta_yoy"] = None

        # Cash burn check: is operating cash flow negative?
        # Use net income as proxy if OCF not available
        if income_annual:
            ni = income_annual[0].get("netIncome", 0) or 0
            result["is_cash_burning"] = ni < 0
        else:
            result["is_cash_burning"] = None
    else:
        for k in ["net_cash", "net_cash_to_mkt_cap", "current_ratio",
                   "share_count_delta_yoy", "is_cash_burning"]:
            result[k] = None
        flags.append("DATA_INCOMPLETE")

    # ── Beaten-down entry metrics ──
    high_52w = quote.get("yearHigh", 0) or 0
    low_52w = quote.get("yearLow", 0) or 0
    price = quote.get("price", 0) or 0

    if high_52w > 0 and price > 0:
        result["pct_off_52w_high"] = (price - high_52w) / high_52w * 100
    else:
        result["pct_off_52w_high"] = None

    if low_52w > 0 and price > 0:
        result["pct_above_52w_low"] = (price - low_52w) / low_52w * 100
    else:
        result["pct_above_52w_low"] = None

    result["return_6m"] = price_change.get("6M")

    # ── Under-followed metrics ──
    result["market_cap"] = quote.get("marketCap", 0) or 0
    result["avg_volume"] = quote.get("avgVolume", 0) or 0
    result["avg_dollar_volume"] = (result["avg_volume"] * price) if price else 0

    # Analyst count — use number of analyst estimates as proxy
    result["analyst_count"] = ticker_data.get("analyst_count", None)

    # ── Tailwind tags ──
    desc = profile.get("description", "") or ""
    industry = profile.get("industry", "") or ""
    result["tailwind_tags"] = _tag_structural_tailwind(desc, industry)

    # ── Revenue CAGR (3yr if available) ──
    if len(income_annual) >= 3:
        rev_oldest = income_annual[-1].get("revenue", 0) or 0
        rev_newest = income_annual[0].get("revenue", 0) or 0
        years = len(income_annual) - 1
        if rev_oldest > 0 and rev_newest > 0:
            result["revenue_cagr"] = ((rev_newest / rev_oldest) ** (1 / years) - 1) * 100
        else:
            result["revenue_cagr"] = None
    else:
        result["revenue_cagr"] = None

    result["flags"] = flags
    return result


def _check_balance_sheet_gate(inputs: dict) -> bool:
    """Return True if the balance-sheet gate trips (bad sign).

    The gate is the trait that separated winners from traps. It trips only
    when all three risk conditions coincide: weak cash position, active cash
    burn, and heavy dilution. The winners survive because they fail at least
    one leg (e.g. MRAM holds net cash and isn't diluting; ADTN is profitable).
    """
    gate = CONFIG["gate"]
    net_cash = inputs.get("net_cash")
    nc_to_cap = inputs.get("net_cash_to_mkt_cap")
    is_burning = inputs.get("is_cash_burning")
    dilution = inputs.get("share_count_delta_yoy")

    # Cash weakness = outright negative net cash OR a positive-but-thin cushion
    # that can't sustain the burn for long without another raise.
    has_negative_net_cash = net_cash is not None and net_cash < 0
    has_thin_cushion = (
        nc_to_cap is not None and nc_to_cap < gate["weak_net_cash_to_mktcap"]
    )
    has_cash_weakness = has_negative_net_cash or has_thin_cushion

    has_cash_burn = is_burning is True
    has_heavy_dilution = (
        dilution is not None and dilution > gate["dilution_yoy_threshold"]
    )

    return has_cash_weakness and has_cash_burn and has_heavy_dilution


def compute_subscores_absolute(inputs: dict) -> dict[str, float]:
    """Compute the six sub-scores using absolute thresholds calibrated to winners."""
    default = CONFIG["missing_field_subscore"]

    # 1. Cyclical / Profitability Inflection (25%)
    # Higher for: positive YoY rev growth after decline, QoQ acceleration, margin crossing positive
    inflection = default
    rev_g = inputs.get("revenue_growth_yoy")
    accel = inputs.get("revenue_acceleration")
    crossing = inputs.get("net_margin_crossing_positive", False)
    had_decline = inputs.get("had_prior_decline", False)

    components = []
    if rev_g is not None:
        # 0% = 30, 20% = 60, 50%+ = 100
        rev_score = min(100, max(0, 30 + rev_g * 1.4))
        if had_decline and rev_g > 0:
            rev_score = min(100, rev_score + 15)  # bonus for turnaround
        components.append(rev_score)
    if accel is not None:
        accel_score = min(100, max(0, 50 + accel * 2.5))
        components.append(accel_score)
    if crossing:
        components.append(90)
    if components:
        inflection = sum(components) / len(components)

    # 2. Structural Tailwind (20%)
    # Tag match is the primary signal — the company operates in a secular
    # growth lane. Trailing CAGR can *boost* if positive but never drag,
    # because the winners we're looking for are coming out of troughs where
    # trailing CAGR is mechanically negative through no fault of the tailwind.
    tags = inputs.get("tailwind_tags", [])
    cagr = inputs.get("revenue_cagr")
    tailwind = default
    tag_score = min(100, len(tags) * 35) if tags else 0
    cagr_bonus = 0
    if cagr is not None and cagr > 0:
        cagr_bonus = min(30, cagr * 1.2)  # 25% CAGR = +30pt bonus, capped
    tailwind = min(100, tag_score + cagr_bonus) if tags else (cagr_bonus * 0.5)

    # 3. Margin Inflection (20%)
    gm_delta = inputs.get("gross_margin_delta_yoy")
    om_delta = inputs.get("op_margin_delta_yoy")
    incr_margin = inputs.get("incremental_margin")
    margin_components = []
    if gm_delta is not None:
        # +5pp = 80, +10pp = 100, -5pp = 20
        margin_components.append(min(100, max(0, 50 + gm_delta * 6)))
    if om_delta is not None:
        margin_components.append(min(100, max(0, 50 + om_delta * 5)))
    if incr_margin is not None:
        margin_components.append(min(100, max(0, incr_margin * 2)))
    margin = sum(margin_components) / len(margin_components) if margin_components else default

    # 4. Balance-Sheet Quality (20%)
    net_cash = inputs.get("net_cash")
    nc_mkt = inputs.get("net_cash_to_mkt_cap")
    cr = inputs.get("current_ratio")
    dilution = inputs.get("share_count_delta_yoy")
    bs_components = []
    if net_cash is not None:
        bs_components.append(80 if net_cash > 0 else max(0, 40 + net_cash / 1_000_000))
    if nc_mkt is not None:
        bs_components.append(min(100, max(0, 50 + nc_mkt * 200)))
    if cr is not None:
        bs_components.append(min(100, max(0, cr * 35)))  # 2.0 = 70, 3.0 = 100
    if dilution is not None:
        # Lower dilution = better: 0% = 90, -5% = 100, +15% = 20
        bs_components.append(min(100, max(0, 90 - dilution * 400)))
    balance_sheet = sum(bs_components) / len(bs_components) if bs_components else default

    # 5. Beaten-Down Entry (10%)
    pct_off_high = inputs.get("pct_off_52w_high")
    ret_6m = inputs.get("return_6m")
    pct_above_low = inputs.get("pct_above_52w_low")
    bd_components = []
    if pct_off_high is not None:
        # More negative = better: -50% off high = 100, 0% = 20
        bd_components.append(min(100, max(0, 20 + abs(pct_off_high) * 1.6)))
    if ret_6m is not None:
        # More negative 6M return = higher score (beaten down)
        bd_components.append(min(100, max(0, 50 - ret_6m * 1.0)))
    if pct_above_low is not None:
        # Closer to low = higher: 0% above low = 100, 200% above = 0
        bd_components.append(min(100, max(0, 100 - pct_above_low * 0.5)))
    beaten_down = sum(bd_components) / len(bd_components) if bd_components else default

    # 6. Under-Followed / Re-rate Room (5%)
    mkt_cap = inputs.get("market_cap", 0)
    analyst = inputs.get("analyst_count")
    dollar_vol = inputs.get("avg_dollar_volume", 0)
    uf_components = []
    if mkt_cap > 0:
        # Smaller = higher: $100M = 90, $500M = 60, $2B = 30, $10B = 10
        uf_components.append(min(100, max(0, 100 - (mkt_cap / 1e9) * 10)))
    if analyst is not None:
        # Fewer = higher: 0 = 100, 5 = 60, 15+ = 10
        uf_components.append(min(100, max(0, 100 - analyst * 6)))
    if dollar_vol > 0:
        # Lower daily $ vol = more under-followed
        uf_components.append(min(100, max(0, 100 - (dollar_vol / 1e7) * 10)))
    under_followed = sum(uf_components) / len(uf_components) if uf_components else default

    return {
        "inflection": round(min(100, max(0, inflection)), 1),
        "tailwind": round(min(100, max(0, tailwind)), 1),
        "margin": round(min(100, max(0, margin)), 1),
        "balance_sheet": round(min(100, max(0, balance_sheet)), 1),
        "beaten_down": round(min(100, max(0, beaten_down)), 1),
        "under_followed": round(min(100, max(0, under_followed)), 1),
    }


def _classify_archetype(subscores: dict[str, float]) -> tuple[str, float]:
    """Classify candidate against winner archetypes via cosine similarity."""
    if not ARCHETYPE_VECTORS:
        return "none", 0.0

    best_match = "none"
    best_sim = 0.0
    for name, ref_vector in ARCHETYPE_VECTORS.items():
        sim = _cosine_similarity(subscores, ref_vector)
        if sim > best_sim:
            best_sim = sim
            best_match = name

    if best_sim < CONFIG["archetype_min_cosine"]:
        return "none", best_sim

    return best_match, best_sim


def compute_winner_pattern_score(inputs: dict) -> dict:
    """Score one candidate against the winner pattern.

    `inputs` should come from extract_wps_inputs().
    """
    flags = list(inputs.get("flags", []))
    subscores = compute_subscores_absolute(inputs)

    # Weighted total
    weights = CONFIG["weights"]
    wps = sum(subscores[k] * weights[k] for k in weights)

    # Balance-sheet gate
    gate_tripped = _check_balance_sheet_gate(inputs)
    if gate_tripped:
        wps = min(wps, CONFIG["gate"]["wps_cap_if_tripped"])

    # Archetype classification
    pattern_match, cosine_sim = _classify_archetype(subscores)

    return {
        "wps": round(wps, 1),
        "subscores": subscores,
        "balance_sheet_gate": gate_tripped,
        "pattern_match": pattern_match,
        "cosine_similarity": round(cosine_sim, 3),
        "flags": flags,
    }


def compute_reference_vectors(reference_results: dict[str, dict]) -> dict[str, dict[str, float]]:
    """Compute and freeze archetype reference vectors from validation run.

    reference_results: {ticker: wps_result_dict}
    """
    global ARCHETYPE_VECTORS

    archetype_map = {
        "MXL": "MXL-type",
        "MRAM": "MRAM-type",
        "XMTR": "XMTR-type",
        "ADTN": "ADTN-type",
    }

    vectors = {}
    for ticker, arch_name in archetype_map.items():
        if ticker in reference_results:
            vectors[arch_name] = reference_results[ticker]["subscores"]

    ARCHETYPE_VECTORS.update(vectors)
    return vectors


def explain_wps(ticker: str, inputs: dict, result: dict) -> str:
    """Generate human-readable explanation of WPS scoring."""
    lines = [
        f"{ticker}  {inputs.get('ticker', '')}        WPS {result['wps']}   Pattern: {result['pattern_match']}",
    ]
    sub = result["subscores"]

    explanations = {
        "inflection": [],
        "tailwind": [],
        "margin": [],
        "balance_sheet": [],
        "beaten_down": [],
        "under_followed": [],
    }

    rg = inputs.get("revenue_growth_yoy")
    if rg is not None:
        explanations["inflection"].append(f"rev growth {rg:+.0f}% YoY")
    if inputs.get("had_prior_decline"):
        explanations["inflection"].append("turnaround from prior decline")
    if inputs.get("net_margin_crossing_positive"):
        explanations["inflection"].append("net margin crossed positive")

    tags = inputs.get("tailwind_tags", [])
    if tags:
        explanations["tailwind"].append(f"tags: {', '.join(tags)}")
    cagr = inputs.get("revenue_cagr")
    if cagr:
        explanations["tailwind"].append(f"CAGR {cagr:.0f}%")

    gm_d = inputs.get("gross_margin_delta_yoy")
    if gm_d is not None:
        explanations["margin"].append(f"GM Δ {gm_d:+.1f}pp YoY")
    om_d = inputs.get("op_margin_delta_yoy")
    if om_d is not None:
        explanations["margin"].append(f"OpM Δ {om_d:+.1f}pp")

    nc = inputs.get("net_cash")
    if nc is not None:
        explanations["balance_sheet"].append(f"net cash ${nc/1e6:,.0f}M")
    cr = inputs.get("current_ratio")
    if cr is not None:
        explanations["balance_sheet"].append(f"CR {cr:.1f}")
    dil = inputs.get("share_count_delta_yoy")
    if dil is not None:
        explanations["balance_sheet"].append(f"dilution {dil*100:+.1f}%")

    off_high = inputs.get("pct_off_52w_high")
    if off_high is not None:
        explanations["beaten_down"].append(f"{off_high:+.0f}% from 52w high")
    ret6 = inputs.get("return_6m")
    if ret6 is not None:
        explanations["beaten_down"].append(f"6M return {ret6:+.0f}%")

    mkt = inputs.get("market_cap", 0)
    explanations["under_followed"].append(f"mkt cap ${mkt/1e6:,.0f}M")

    for trait, score in sub.items():
        detail = "; ".join(explanations.get(trait, []))
        lines.append(f"  {trait:<20s} {score:5.0f}  {detail}")

    gate_status = "TRIPPED — WPS capped" if result["balance_sheet_gate"] else "PASS"
    flag_str = ", ".join(result["flags"]) if result["flags"] else "none"
    lines.append(f"  Gate: {gate_status}   Flags: {flag_str}")

    return "\n".join(lines)
