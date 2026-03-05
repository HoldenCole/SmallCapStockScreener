"""Export screener results to an institutional-quality Excel workbook."""

import os
import sys
from datetime import datetime
from typing import Any

import xlsxwriter

from config import (
    DEFAULT_WEIGHTS,
    DESCRIPTION_CHECK_SECTORS,
    FMP_API_KEY,
    TIERS,
    TOP_N_RESULTS,
)
from screener.filters import (
    _description_matches,
    apply_hard_filters,
    apply_sanity_filters,
    is_excluded_industry,
    passes_quality_floor,
    score_stock,
)
from screener.fingerprint import (
    compute_fingerprint_score,
    compute_ideal_ranges,
    load_reference_stocks,
)
from screener.fmp_client import FMPClient
from screener.utils import compute_dilution, compute_revenue_metrics

import pandas as pd


# ── Helper: classify investment thesis ────────────────────────────────

def _classify_thesis(s: dict) -> dict[str, str]:
    """Return conviction level, thesis bucket, and rationale for a stock."""
    combined = s["combined"]
    rev = s["rev_growth_pct"] or 0
    gm = s["gross_margin_pct"] or 0
    dil = s["dilution_3yr_pct"] or 0
    insider = s["insider_pct"] or 0
    fp = s["fingerprint"]
    mom_3m = s.get("3m_pct") or 0

    # Strengths / risks
    strengths: list[str] = []
    risks: list[str] = []

    if rev >= 40:
        strengths.append("strong revenue growth")
    elif rev >= 20:
        strengths.append("solid revenue growth")
    if gm >= 50:
        strengths.append("high-margin business")
    elif gm >= 35:
        strengths.append("healthy margins")
    if insider >= 15:
        strengths.append("high insider alignment")
    if dil <= 5:
        strengths.append("minimal dilution")
    if fp >= 80:
        strengths.append("strong fingerprint match")
    if mom_3m >= 30:
        strengths.append("positive momentum")

    if dil >= 15:
        risks.append("significant dilution")
    if gm < 30:
        risks.append("thin margins")
    if insider < 5:
        risks.append("low insider ownership")
    if mom_3m <= -30:
        risks.append("negative momentum")
    if rev < 15:
        risks.append("modest growth")

    # Conviction
    if combined >= 85 and len(risks) <= 1:
        conviction = "High"
    elif combined >= 75:
        conviction = "Medium-High"
    elif combined >= 65:
        conviction = "Medium"
    else:
        conviction = "Speculative"

    # Thesis bucket
    if rev >= 40 and gm >= 40:
        bucket = "High-Growth Quality"
    elif fp >= 80 and rev >= 20:
        bucket = "Fingerprint Match"
    elif insider >= 20 and dil <= 5:
        bucket = "Insider Conviction"
    elif mom_3m >= 50:
        bucket = "Momentum Breakout"
    elif rev >= 30:
        bucket = "Growth Inflection"
    else:
        bucket = "Emerging Watch"

    rationale = (
        f"Strengths: {', '.join(strengths) if strengths else 'N/A'}. "
        f"Risks: {', '.join(risks) if risks else 'none significant'}."
    )

    return {
        "conviction": conviction,
        "bucket": bucket,
        "rationale": rationale,
        "strengths": "; ".join(strengths),
        "risks": "; ".join(risks),
    }


# ── Helper: portfolio construction metrics ────────────────────────────

def _position_size_pct(conviction: str, tier_name: str) -> float:
    """Suggest position size as % of a model portfolio."""
    base = {"High": 8.0, "Medium-High": 5.0, "Medium": 3.0, "Speculative": 1.5}
    size = base.get(conviction, 2.0)
    # Nano caps get reduced sizing for risk management
    if "Nano" in tier_name:
        size *= 0.6
    elif "Breakout" in tier_name:
        size *= 1.2
    return round(size, 1)


def _risk_category(s: dict) -> str:
    dil = s.get("dilution_3yr_pct") or 0
    gm = s.get("gross_margin_pct") or 0
    mkt = s.get("mkt_cap_m") or 0
    if mkt < 100 or dil > 20 or gm < 25:
        return "High"
    if mkt < 500 or dil > 10 or gm < 35:
        return "Medium-High"
    if mkt < 2000:
        return "Medium"
    return "Medium-Low"


# ── Sensitivity analysis helpers ──────────────────────────────────────

SENSITIVITY_PROFILES = {
    "Baseline": DEFAULT_WEIGHTS,
    "Growth Focus": {
        "revenue_growth": 0.40, "gross_margin": 0.15, "dilution": 0.15,
        "insider_ownership": 0.10, "revenue_acceleration": 0.20,
    },
    "Quality Focus": {
        "revenue_growth": 0.20, "gross_margin": 0.30, "dilution": 0.25,
        "insider_ownership": 0.15, "revenue_acceleration": 0.10,
    },
    "Insider Focus": {
        "revenue_growth": 0.20, "gross_margin": 0.15, "dilution": 0.20,
        "insider_ownership": 0.35, "revenue_acceleration": 0.10,
    },
}


# ── Data collection ───────────────────────────────────────────────────

def collect_all_data(client: FMPClient) -> dict[str, list[dict]]:
    """Run the screener pipeline for all tiers, return enriched stock dicts."""
    all_tiers: dict[str, list[dict]] = {}

    for tier_name, tier in TIERS.items():
        print(f"  Screening {tier_name}...")
        raw = client.screen_stocks(tier["min"], tier["max"])
        df = pd.DataFrame(raw)
        if df.empty:
            all_tiers[tier_name] = []
            continue
        if "description" not in df.columns:
            df["description"] = ""

        whitelist_df = apply_hard_filters(df)

        non_wl = df[
            ~df["symbol"].isin(whitelist_df["symbol"])
            & df["sector"].isin(DESCRIPTION_CHECK_SECTORS)
            & ~df["industry"].fillna("").apply(is_excluded_industry)
        ]
        desc_extras: list[str] = []
        if not non_wl.empty:
            for sym in non_wl["symbol"].tolist():
                profile = client.get_profile(sym)
                if _description_matches(profile.get("description", "")):
                    desc_extras.append(sym)

        if desc_extras:
            extra_df = df[df["symbol"].isin(desc_extras)]
            df = pd.concat([whitelist_df, extra_df], ignore_index=True)
        else:
            df = whitelist_df

        if df.empty:
            all_tiers[tier_name] = []
            continue

        results: list[dict] = []
        tickers = df["symbol"].tolist()
        total = len(tickers)

        for i, ticker in enumerate(tickers):
            if (i + 1) % 10 == 0 or i == 0:
                print(f"    [{i+1}/{total}] {ticker}")
            row = df[df["symbol"] == ticker].iloc[0].to_dict()

            income = client.get_income_statements(ticker, quarters=8)
            rev_metrics = compute_revenue_metrics(income)
            ev_data = client.get_enterprise_values(ticker, quarters=12)
            dil_metrics = compute_dilution(ev_data)
            float_data = client.get_shares_float(ticker)
            free_float = float_data.get("freeFloat")
            insider_pct = None
            if isinstance(free_float, (int, float)) and 0 < free_float <= 100:
                insider_pct = round(100.0 - free_float, 1)

            mkt_cap_m = row.get("marketCap", 0) / 1_000_000

            stock_metrics = {
                "revenue_growth_pct": rev_metrics["revenue_growth_pct"],
                "gross_margin_pct": rev_metrics["gross_margin_pct"],
                "dilution_3yr_pct": dil_metrics["dilution_3yr_pct"],
                "insider_ownership_pct": insider_pct,
                "revenue_acceleration_pct": rev_metrics["revenue_acceleration_pct"],
                "market_cap_M": mkt_cap_m,
            }

            if not apply_sanity_filters(stock_metrics):
                continue
            if not passes_quality_floor(stock_metrics):
                continue

            composite = score_stock(stock_metrics, DEFAULT_WEIGHTS)
            fingerprint = compute_fingerprint_score(stock_metrics)
            combined = round(composite * 0.6 + fingerprint * 0.4, 1)

            quote = client.get_quote(ticker)
            price_data = client.get_price_change(ticker)
            profile = client.get_profile(ticker)

            stock_price = quote.get("price")
            description = profile.get("description", "")

            # Sensitivity scores
            sensitivity_scores: dict[str, float] = {}
            for profile_name, weights in SENSITIVITY_PROFILES.items():
                c = score_stock(stock_metrics, weights)
                sensitivity_scores[profile_name] = round(c * 0.6 + fingerprint * 0.4, 1)

            stock = {
                "ticker": ticker,
                "name": row.get("companyName", ""),
                "mkt_cap_m": round(mkt_cap_m, 1),
                "price": stock_price,
                "sector": row.get("sector", ""),
                "industry": row.get("industry", ""),
                "description": description,
                "rev_growth_pct": rev_metrics["revenue_growth_pct"],
                "rev_accel_pct": rev_metrics["revenue_acceleration_pct"],
                "gross_margin_pct": rev_metrics["gross_margin_pct"],
                "dilution_3yr_pct": dil_metrics["dilution_3yr_pct"],
                "insider_pct": insider_pct,
                "composite": composite,
                "fingerprint": fingerprint,
                "combined": combined,
                "1m_pct": price_data.get("1M"),
                "3m_pct": price_data.get("3M"),
                "6m_pct": price_data.get("6M"),
                "1y_pct": price_data.get("1Y"),
                "ytd_pct": price_data.get("ytd"),
                "volume": quote.get("volume"),
                "avg_volume": quote.get("avgVolume"),
                "pe_ratio": quote.get("pe"),
                "eps": quote.get("eps"),
                "52w_high": quote.get("yearHigh"),
                "52w_low": quote.get("yearLow"),
                "tier": tier_name,
                "quarterly_revenue": rev_metrics["quarterly_revenue"],
                "quarterly_dates": rev_metrics["quarterly_dates"],
                "quarterly_gm": rev_metrics["quarterly_gross_margin"],
                "shares_outstanding": dil_metrics["shares_outstanding"],
                "shares_dates": dil_metrics["shares_dates"],
                "sensitivity_scores": sensitivity_scores,
            }
            results.append(stock)

        results.sort(key=lambda x: x["combined"], reverse=True)
        results = results[:TOP_N_RESULTS]
        all_tiers[tier_name] = results
        print(f"    → {len(results)} stocks passed for {tier_name}")

    return all_tiers


# ── Excel writer ──────────────────────────────────────────────────────

def write_excel(all_tiers: dict[str, list[dict]], output_path: str) -> None:
    """Write the institutional-quality Excel workbook."""
    wb = xlsxwriter.Workbook(output_path, {"nan_inf_to_errors": True})

    # ── Formats ───────────────────────────────────────────────────
    fmt_title = wb.add_format({
        "bold": True, "font_size": 16, "font_color": "#1a1a2e",
        "bottom": 2, "bottom_color": "#1a1a2e",
    })
    fmt_subtitle = wb.add_format({
        "bold": True, "font_size": 11, "font_color": "#555555", "italic": True,
    })
    fmt_header = wb.add_format({
        "bold": True, "font_size": 10, "bg_color": "#1a1a2e",
        "font_color": "#ffffff", "border": 1, "text_wrap": True,
        "valign": "vcenter", "align": "center",
    })
    fmt_header_left = wb.add_format({
        "bold": True, "font_size": 10, "bg_color": "#1a1a2e",
        "font_color": "#ffffff", "border": 1, "text_wrap": True,
        "valign": "vcenter",
    })
    fmt_num = wb.add_format({
        "num_format": "#,##0.0", "border": 1, "font_size": 10,
        "valign": "vcenter",
    })
    fmt_int = wb.add_format({
        "num_format": "#,##0", "border": 1, "font_size": 10,
        "valign": "vcenter",
    })
    fmt_pct = wb.add_format({
        "num_format": "0.0%", "border": 1, "font_size": 10,
        "valign": "vcenter",
    })
    fmt_pct_display = wb.add_format({
        "num_format": "0.0", "border": 1, "font_size": 10,
        "valign": "vcenter",
    })
    fmt_dollar = wb.add_format({
        "num_format": "$#,##0.00", "border": 1, "font_size": 10,
        "valign": "vcenter",
    })
    fmt_text = wb.add_format({
        "border": 1, "font_size": 10, "text_wrap": True,
        "valign": "vcenter",
    })
    fmt_text_center = wb.add_format({
        "border": 1, "font_size": 10, "align": "center",
        "valign": "vcenter",
    })
    fmt_bold_text = wb.add_format({
        "bold": True, "border": 1, "font_size": 10,
        "valign": "vcenter",
    })
    fmt_score = wb.add_format({
        "num_format": "0", "border": 1, "font_size": 10,
        "valign": "vcenter", "align": "center",
    })
    fmt_mktcap = wb.add_format({
        "num_format": "$#,##0", "border": 1, "font_size": 10,
        "valign": "vcenter",
    })
    fmt_wrap = wb.add_format({
        "text_wrap": True, "border": 1, "font_size": 10,
        "valign": "top",
    })
    fmt_section = wb.add_format({
        "bold": True, "font_size": 12, "font_color": "#1a1a2e",
        "bottom": 1, "bottom_color": "#cccccc",
    })
    # Conviction colors
    fmt_high = wb.add_format({
        "bg_color": "#c6efce", "font_color": "#006100",
        "border": 1, "font_size": 10, "bold": True,
        "valign": "vcenter", "align": "center",
    })
    fmt_med_high = wb.add_format({
        "bg_color": "#d9ead3", "font_color": "#38761d",
        "border": 1, "font_size": 10, "bold": True,
        "valign": "vcenter", "align": "center",
    })
    fmt_med = wb.add_format({
        "bg_color": "#fff2cc", "font_color": "#7f6000",
        "border": 1, "font_size": 10, "bold": True,
        "valign": "vcenter", "align": "center",
    })
    fmt_spec = wb.add_format({
        "bg_color": "#fce5cd", "font_color": "#783f04",
        "border": 1, "font_size": 10, "bold": True,
        "valign": "vcenter", "align": "center",
    })
    conviction_fmts = {
        "High": fmt_high, "Medium-High": fmt_med_high,
        "Medium": fmt_med, "Speculative": fmt_spec,
    }
    # Risk colors
    fmt_risk_high = wb.add_format({
        "bg_color": "#f4cccc", "font_color": "#990000",
        "border": 1, "font_size": 10, "align": "center", "valign": "vcenter",
    })
    fmt_risk_medhigh = wb.add_format({
        "bg_color": "#fce5cd", "font_color": "#783f04",
        "border": 1, "font_size": 10, "align": "center", "valign": "vcenter",
    })
    fmt_risk_med = wb.add_format({
        "bg_color": "#fff2cc", "font_color": "#7f6000",
        "border": 1, "font_size": 10, "align": "center", "valign": "vcenter",
    })
    fmt_risk_medlow = wb.add_format({
        "bg_color": "#d9ead3", "font_color": "#38761d",
        "border": 1, "font_size": 10, "align": "center", "valign": "vcenter",
    })
    risk_fmts = {
        "High": fmt_risk_high, "Medium-High": fmt_risk_medhigh,
        "Medium": fmt_risk_med, "Medium-Low": fmt_risk_medlow,
    }
    # Alternating row formats
    fmt_alt = wb.add_format({
        "bg_color": "#f5f5f5", "border": 1, "font_size": 10,
        "valign": "vcenter",
    })

    run_date = datetime.now().strftime("%Y-%m-%d %H:%M")
    all_stocks = []
    for tier_name, stocks in all_tiers.items():
        for s in stocks:
            all_stocks.append(s)

    # ================================================================
    # TAB 1: Executive Summary
    # ================================================================
    ws = wb.add_worksheet("Executive Summary")
    ws.hide_gridlines(2)
    ws.set_column("A:A", 3)
    ws.set_column("B:B", 18)
    ws.set_column("C:G", 14)

    ws.merge_range("B2:G2", "Stock Screener — Executive Summary", fmt_title)
    ws.merge_range("B3:G3", f"Generated {run_date}  |  Structural Growth Screener", fmt_subtitle)

    row = 4
    for tier_name, stocks in all_tiers.items():
        row += 1
        ws.merge_range(row, 1, row, 6, tier_name, fmt_section)
        row += 1

        headers = ["Ticker", "Company", "Mkt Cap ($M)", "Score", "Fingerprint", "Conviction"]
        for c, h in enumerate(headers):
            ws.write(row, 1 + c, h, fmt_header)
        row += 1

        for s in stocks:
            thesis = _classify_thesis(s)
            ws.write(row, 1, s["ticker"], fmt_bold_text)
            ws.write(row, 2, s["name"], fmt_text)
            ws.write(row, 3, s["mkt_cap_m"], fmt_mktcap)
            ws.write(row, 4, s["combined"], fmt_score)
            ws.write(row, 5, s["fingerprint"], fmt_score)
            ws.write(row, 6, thesis["conviction"],
                     conviction_fmts.get(thesis["conviction"], fmt_text_center))
            row += 1

        row += 1

    # Key stats summary
    if all_stocks:
        row += 1
        ws.merge_range(row, 1, row, 6, "Portfolio Overview", fmt_section)
        row += 1
        ws.write(row, 1, "Total Candidates", fmt_bold_text)
        ws.write(row, 2, len(all_stocks), fmt_int)
        row += 1
        ws.write(row, 1, "Avg Combined Score", fmt_bold_text)
        ws.write(row, 2, round(sum(s["combined"] for s in all_stocks) / len(all_stocks), 1), fmt_num)
        row += 1
        ws.write(row, 1, "Avg Rev Growth", fmt_bold_text)
        rgs = [s["rev_growth_pct"] for s in all_stocks if s["rev_growth_pct"] is not None]
        ws.write(row, 2, f"{sum(rgs)/len(rgs):.1f}%" if rgs else "N/A", fmt_text)
        row += 1
        ws.write(row, 1, "Avg Gross Margin", fmt_bold_text)
        gms = [s["gross_margin_pct"] for s in all_stocks if s["gross_margin_pct"] is not None]
        ws.write(row, 2, f"{sum(gms)/len(gms):.1f}%" if gms else "N/A", fmt_text)

    # ================================================================
    # TABS 2-4: Tier-specific screener results
    # ================================================================
    tier_short = {
        "Nano Cap ($50M–$300M)": "Nano Cap",
        "Small Cap ($300M–$2B)": "Small Cap",
        "Breakout ($2B–$15B)": "Breakout",
    }

    for tier_name, stocks in all_tiers.items():
        short = tier_short.get(tier_name, tier_name[:20])
        ws = wb.add_worksheet(short)
        ws.hide_gridlines(2)
        ws.freeze_panes(4, 2)

        ws.merge_range("A1:R1", f"{tier_name} — Screener Results", fmt_title)
        ws.merge_range("A2:R2", f"Top {len(stocks)} by combined score  |  {run_date}", fmt_subtitle)

        headers = [
            "Ticker", "Company", "Industry", "Price",
            "Mkt Cap ($M)", "Rev Growth %", "Rev Accel %",
            "Gross Margin %", "Dilution 3yr %", "Insider %",
            "Composite", "Fingerprint", "Combined",
            "1M %", "3M %", "6M %", "YTD %", "1Y %",
        ]
        widths = [8, 28, 22, 10, 12, 12, 12, 12, 12, 10, 10, 10, 10, 8, 8, 8, 8, 8]
        for c, (h, w) in enumerate(zip(headers, widths)):
            ws.set_column(c, c, w)
            ws.write(3, c, h, fmt_header if c >= 3 else fmt_header_left)

        for r, s in enumerate(stocks):
            row = 4 + r
            ws.write(row, 0, s["ticker"], fmt_bold_text)
            ws.write(row, 1, s["name"], fmt_text)
            ws.write(row, 2, s["industry"], fmt_text)
            ws.write(row, 3, s["price"], fmt_dollar)
            ws.write(row, 4, s["mkt_cap_m"], fmt_mktcap)
            ws.write(row, 5, s["rev_growth_pct"], fmt_pct_display)
            ws.write(row, 6, s["rev_accel_pct"], fmt_pct_display)
            ws.write(row, 7, s["gross_margin_pct"], fmt_pct_display)
            ws.write(row, 8, s["dilution_3yr_pct"], fmt_pct_display)
            ws.write(row, 9, s["insider_pct"], fmt_pct_display)
            ws.write(row, 10, s["composite"], fmt_score)
            ws.write(row, 11, s["fingerprint"], fmt_score)
            ws.write(row, 12, s["combined"], fmt_score)
            ws.write(row, 13, s.get("1m_pct"), fmt_pct_display)
            ws.write(row, 14, s.get("3m_pct"), fmt_pct_display)
            ws.write(row, 15, s.get("6m_pct"), fmt_pct_display)
            ws.write(row, 16, s.get("ytd_pct"), fmt_pct_display)
            ws.write(row, 17, s.get("1y_pct"), fmt_pct_display)

        # Conditional formatting: score columns
        if stocks:
            last_row = 4 + len(stocks) - 1
            for col in [10, 11, 12]:
                ws.conditional_format(4, col, last_row, col, {
                    "type": "3_color_scale",
                    "min_color": "#f4cccc",
                    "mid_color": "#fff2cc",
                    "max_color": "#c6efce",
                })
            # Performance columns
            for col in [13, 14, 15, 16, 17]:
                ws.conditional_format(4, col, last_row, col, {
                    "type": "3_color_scale",
                    "min_color": "#f4cccc",
                    "mid_color": "#ffffff",
                    "max_color": "#c6efce",
                })

    # ================================================================
    # TAB 5: Company Descriptions
    # ================================================================
    ws = wb.add_worksheet("Descriptions")
    ws.hide_gridlines(2)
    ws.set_column("A:A", 8)
    ws.set_column("B:B", 22)
    ws.set_column("C:C", 18)
    ws.set_column("D:D", 18)
    ws.set_column("E:E", 14)
    ws.set_column("F:F", 80)

    ws.merge_range("A1:F1", "Company Descriptions & Profiles", fmt_title)
    ws.merge_range("A2:F2", f"All screened companies  |  {run_date}", fmt_subtitle)

    headers = ["Ticker", "Company", "Sector", "Industry", "Tier", "Description"]
    for c, h in enumerate(headers):
        ws.write(3, c, h, fmt_header_left if c < 5 else fmt_header)

    row = 4
    for tier_name, stocks in all_tiers.items():
        for s in stocks:
            ws.write(row, 0, s["ticker"], fmt_bold_text)
            ws.write(row, 1, s["name"], fmt_text)
            ws.write(row, 2, s["sector"], fmt_text)
            ws.write(row, 3, s["industry"], fmt_text)
            ws.write(row, 4, tier_short.get(tier_name, tier_name), fmt_text_center)
            # Truncate description for Excel cell limits
            desc = (s.get("description") or "")[:1000]
            ws.write(row, 5, desc, fmt_wrap)
            ws.set_row(row, 60)
            row += 1

    # ================================================================
    # TAB 6: Standouts
    # ================================================================
    ws = wb.add_worksheet("Standouts")
    ws.hide_gridlines(2)
    ws.set_column("A:A", 3)
    ws.set_column("B:B", 24)
    ws.set_column("C:H", 14)

    ws.merge_range("B1:H1", "Cross-Tier Standouts", fmt_title)
    ws.merge_range("B2:H2", f"Top performers across all metrics  |  {run_date}", fmt_subtitle)

    def _write_ranking(ws, start_row: int, title: str, ranked: list[dict],
                       value_key: str, value_label: str, fmt_val) -> int:
        r = start_row
        ws.merge_range(r, 1, r, 7, title, fmt_section)
        r += 1
        rank_headers = ["Rank", "Ticker", "Company", "Tier", value_label, "Combined Score"]
        for c, h in enumerate(rank_headers):
            ws.write(r, 1 + c, h, fmt_header)
        r += 1
        for i, s in enumerate(ranked[:5]):
            ws.write(r, 1, i + 1, fmt_score)
            ws.write(r, 2, s["ticker"], fmt_bold_text)
            ws.write(r, 3, s["name"], fmt_text)
            ws.write(r, 4, tier_short.get(s["tier"], s["tier"]), fmt_text_center)
            val = s.get(value_key)
            if val is not None:
                ws.write(r, 5, val, fmt_val)
            else:
                ws.write(r, 5, "N/A", fmt_text_center)
            ws.write(r, 6, s["combined"], fmt_score)
            r += 1
        return r + 1

    row = 3
    # Highest combined score
    by_score = sorted(all_stocks, key=lambda x: x["combined"], reverse=True)
    row = _write_ranking(ws, row, "Highest Combined Score", by_score,
                         "combined", "Score", fmt_score)

    # Best fingerprint
    by_fp = sorted(all_stocks, key=lambda x: x["fingerprint"], reverse=True)
    row = _write_ranking(ws, row, "Best Fingerprint Match (Most Like Pre-Run Winners)",
                         by_fp, "fingerprint", "Fingerprint", fmt_score)

    # Fastest revenue growth
    by_rev = sorted([s for s in all_stocks if s.get("rev_growth_pct") is not None],
                    key=lambda x: x["rev_growth_pct"], reverse=True)
    row = _write_ranking(ws, row, "Fastest Revenue Growth", by_rev,
                         "rev_growth_pct", "Rev Growth %", fmt_pct_display)

    # Strongest momentum
    by_mom = sorted([s for s in all_stocks if s.get("3m_pct") is not None],
                    key=lambda x: x["3m_pct"], reverse=True)
    row = _write_ranking(ws, row, "Highest 3-Month Momentum", by_mom,
                         "3m_pct", "3M Change %", fmt_pct_display)

    # Lowest dilution
    by_dil = sorted([s for s in all_stocks if s.get("dilution_3yr_pct") is not None],
                    key=lambda x: x["dilution_3yr_pct"])
    row = _write_ranking(ws, row, "Lowest Dilution (Capital Discipline)", by_dil,
                         "dilution_3yr_pct", "Dilution 3yr %", fmt_pct_display)

    # Highest insider ownership
    by_ins = sorted([s for s in all_stocks if s.get("insider_pct") is not None],
                    key=lambda x: x["insider_pct"], reverse=True)
    row = _write_ranking(ws, row, "Highest Insider Ownership", by_ins,
                         "insider_pct", "Insider %", fmt_pct_display)

    # Smallest (earliest stage)
    by_size = sorted(all_stocks, key=lambda x: x["mkt_cap_m"])
    row = _write_ranking(ws, row, "Smallest Market Cap (Earliest Stage)", by_size,
                         "mkt_cap_m", "Mkt Cap ($M)", fmt_mktcap)

    # ================================================================
    # TAB 7: Investment Recommendations
    # ================================================================
    ws = wb.add_worksheet("Recommendations")
    ws.hide_gridlines(2)
    ws.set_column("A:A", 8)
    ws.set_column("B:B", 24)
    ws.set_column("C:C", 12)
    ws.set_column("D:D", 12)
    ws.set_column("E:E", 18)
    ws.set_column("F:F", 10)
    ws.set_column("G:G", 50)
    ws.set_column("H:H", 35)
    ws.set_column("I:I", 35)

    ws.merge_range("A1:I1", "Investment Recommendations", fmt_title)
    ws.merge_range("A2:I2",
                   f"Conviction levels based on combined scoring, fundamentals, and risk profile  |  {run_date}",
                   fmt_subtitle)

    headers = [
        "Ticker", "Company", "Tier", "Score",
        "Thesis Bucket", "Conviction", "Rationale", "Strengths", "Risks",
    ]
    for c, h in enumerate(headers):
        ws.write(3, c, h, fmt_header_left if c in [0, 1, 6, 7, 8] else fmt_header)

    row = 4
    # Sort all stocks by combined score
    for s in sorted(all_stocks, key=lambda x: x["combined"], reverse=True):
        thesis = _classify_thesis(s)
        ws.write(row, 0, s["ticker"], fmt_bold_text)
        ws.write(row, 1, s["name"], fmt_text)
        ws.write(row, 2, tier_short.get(s["tier"], s["tier"]), fmt_text_center)
        ws.write(row, 3, s["combined"], fmt_score)
        ws.write(row, 4, thesis["bucket"], fmt_text_center)
        ws.write(row, 5, thesis["conviction"],
                 conviction_fmts.get(thesis["conviction"], fmt_text_center))
        ws.write(row, 6, thesis["rationale"], fmt_wrap)
        ws.write(row, 7, thesis["strengths"], fmt_text)
        ws.write(row, 8, thesis["risks"], fmt_text)
        ws.set_row(row, 35)
        row += 1

    # ================================================================
    # TAB 8: Portfolio Construction
    # ================================================================
    ws = wb.add_worksheet("Portfolio Construction")
    ws.hide_gridlines(2)
    ws.set_column("A:A", 3)
    ws.set_column("B:B", 10)
    ws.set_column("C:C", 24)
    ws.set_column("D:N", 14)

    ws.merge_range("B1:N1", "Model Portfolio Construction & Risk Metrics", fmt_title)
    ws.merge_range("B2:N2",
                   f"Position sizing, risk categorization, and portfolio analytics  |  {run_date}",
                   fmt_subtitle)

    # Section 1: Position sizing
    row = 3
    ws.merge_range(row, 1, row, 13, "Suggested Position Sizing", fmt_section)
    row += 1

    headers = [
        "Ticker", "Company", "Tier", "Score", "Conviction",
        "Risk Category", "Position %", "Mkt Cap ($M)", "Price",
        "52W High", "52W Low", "% from 52W High", "Avg Volume",
    ]
    for c, h in enumerate(headers):
        ws.write(row, 1 + c, h, fmt_header)
    row += 1

    total_alloc = 0.0
    for s in sorted(all_stocks, key=lambda x: x["combined"], reverse=True):
        thesis = _classify_thesis(s)
        risk = _risk_category(s)
        pos_pct = _position_size_pct(thesis["conviction"], s["tier"])
        total_alloc += pos_pct

        ws.write(row, 1, s["ticker"], fmt_bold_text)
        ws.write(row, 2, s["name"], fmt_text)
        ws.write(row, 3, tier_short.get(s["tier"], s["tier"]), fmt_text_center)
        ws.write(row, 4, s["combined"], fmt_score)
        ws.write(row, 5, thesis["conviction"],
                 conviction_fmts.get(thesis["conviction"], fmt_text_center))
        ws.write(row, 6, risk, risk_fmts.get(risk, fmt_text_center))
        ws.write(row, 7, pos_pct, fmt_pct_display)
        ws.write(row, 8, s["mkt_cap_m"], fmt_mktcap)
        ws.write(row, 9, s["price"], fmt_dollar)
        ws.write(row, 10, s.get("52w_high"), fmt_dollar)
        ws.write(row, 11, s.get("52w_low"), fmt_dollar)
        # % from 52W high
        if s.get("price") and s.get("52w_high") and s["52w_high"] > 0:
            pct_from_high = ((s["price"] - s["52w_high"]) / s["52w_high"]) * 100
            ws.write(row, 12, round(pct_from_high, 1), fmt_pct_display)
        else:
            ws.write(row, 12, "", fmt_text_center)
        ws.write(row, 13, s.get("avg_volume"), fmt_int)
        row += 1

    # Portfolio totals
    row += 1
    ws.write(row, 1, "Total Allocation", fmt_bold_text)
    ws.write(row, 7, round(total_alloc, 1), fmt_pct_display)
    ws.write(row, 8, f"Cash reserve: {round(100 - total_alloc, 1)}%", fmt_text)

    # Section 2: Sector / theme allocation
    row += 2
    ws.merge_range(row, 1, row, 8, "Sector & Theme Allocation", fmt_section)
    row += 1

    # Count by industry
    industry_counts: dict[str, list[str]] = {}
    for s in all_stocks:
        ind = s.get("industry", "Other")
        industry_counts.setdefault(ind, []).append(s["ticker"])

    ws.write(row, 1, "Industry", fmt_header_left)
    ws.write(row, 2, "Count", fmt_header)
    ws.write(row, 3, "Tickers", fmt_header_left)
    row += 1

    for ind in sorted(industry_counts, key=lambda x: len(industry_counts[x]), reverse=True):
        tickers = industry_counts[ind]
        ws.write(row, 1, ind, fmt_text)
        ws.write(row, 2, len(tickers), fmt_int)
        ws.write(row, 3, ", ".join(tickers), fmt_text)
        row += 1

    # Section 3: Tier allocation
    row += 1
    ws.merge_range(row, 1, row, 6, "Tier Allocation", fmt_section)
    row += 1
    ws.write(row, 1, "Tier", fmt_header_left)
    ws.write(row, 2, "# Positions", fmt_header)
    ws.write(row, 3, "Avg Score", fmt_header)
    ws.write(row, 4, "Avg Rev Growth", fmt_header)
    ws.write(row, 5, "Total Alloc %", fmt_header)
    row += 1

    for tier_name, stocks in all_tiers.items():
        if not stocks:
            continue
        short = tier_short.get(tier_name, tier_name)
        avg_score = sum(s["combined"] for s in stocks) / len(stocks)
        rgs = [s["rev_growth_pct"] for s in stocks if s["rev_growth_pct"] is not None]
        avg_rg = sum(rgs) / len(rgs) if rgs else 0
        tier_alloc = sum(
            _position_size_pct(_classify_thesis(s)["conviction"], tier_name)
            for s in stocks
        )
        ws.write(row, 1, short, fmt_text)
        ws.write(row, 2, len(stocks), fmt_int)
        ws.write(row, 3, round(avg_score, 1), fmt_num)
        ws.write(row, 4, round(avg_rg, 1), fmt_pct_display)
        ws.write(row, 5, round(tier_alloc, 1), fmt_pct_display)
        row += 1

    # Section 4: Risk distribution
    row += 1
    ws.merge_range(row, 1, row, 5, "Risk Distribution", fmt_section)
    row += 1

    risk_counts: dict[str, int] = {}
    for s in all_stocks:
        r = _risk_category(s)
        risk_counts[r] = risk_counts.get(r, 0) + 1

    ws.write(row, 1, "Risk Level", fmt_header_left)
    ws.write(row, 2, "# Positions", fmt_header)
    ws.write(row, 3, "% of Portfolio", fmt_header)
    row += 1
    for risk_level in ["Medium-Low", "Medium", "Medium-High", "High"]:
        count = risk_counts.get(risk_level, 0)
        ws.write(row, 1, risk_level, risk_fmts.get(risk_level, fmt_text))
        ws.write(row, 2, count, fmt_int)
        ws.write(row, 3, round(count / len(all_stocks) * 100, 1) if all_stocks else 0,
                 fmt_pct_display)
        row += 1

    # ================================================================
    # TAB 9: Sensitivity Analysis
    # ================================================================
    ws = wb.add_worksheet("Sensitivity")
    ws.hide_gridlines(2)
    ws.set_column("A:A", 3)
    ws.set_column("B:B", 10)
    ws.set_column("C:C", 24)
    ws.set_column("D:G", 14)
    ws.set_column("H:H", 14)

    ws.merge_range("B1:H1", "Sensitivity Analysis — Weight Profile Comparison", fmt_title)
    ws.merge_range("B2:H2",
                   "How scores change under different weighting assumptions",
                   fmt_subtitle)

    # Show the profiles
    row = 3
    ws.merge_range(row, 1, row, 7, "Weight Profiles Used", fmt_section)
    row += 1
    profile_names = list(SENSITIVITY_PROFILES.keys())
    weight_keys = list(DEFAULT_WEIGHTS.keys())

    ws.write(row, 1, "Weight", fmt_header_left)
    for c, name in enumerate(profile_names):
        ws.write(row, 2 + c, name, fmt_header)
    row += 1

    for wk in weight_keys:
        label = wk.replace("_", " ").title()
        ws.write(row, 1, label, fmt_text)
        for c, pn in enumerate(profile_names):
            ws.write(row, 2 + c, SENSITIVITY_PROFILES[pn][wk], fmt_num)
        row += 1

    # Score comparison table
    row += 1
    ws.merge_range(row, 1, row, 7, "Combined Scores Under Each Profile", fmt_section)
    row += 1

    headers = ["Ticker", "Company"] + profile_names + ["Max Swing"]
    for c, h in enumerate(headers):
        ws.write(row, 1 + c, h, fmt_header if c >= 2 else fmt_header_left)
    row += 1

    for s in sorted(all_stocks, key=lambda x: x["combined"], reverse=True):
        ws.write(row, 1, s["ticker"], fmt_bold_text)
        ws.write(row, 2, s["name"], fmt_text)
        scores = s.get("sensitivity_scores", {})
        vals = []
        for c, pn in enumerate(profile_names):
            v = scores.get(pn, s["combined"])
            ws.write(row, 3 + c, v, fmt_score)
            vals.append(v)
        # Max swing: how much ranking can change
        if vals:
            swing = max(vals) - min(vals)
            ws.write(row, 3 + len(profile_names), round(swing, 1), fmt_num)
        row += 1

    # Conditional formatting on score columns
    if all_stocks:
        last_data_row = row - 1
        header_row = row - len(all_stocks) - 1
        for c in range(3, 3 + len(profile_names)):
            ws.conditional_format(header_row + 1, c, last_data_row, c, {
                "type": "3_color_scale",
                "min_color": "#f4cccc",
                "mid_color": "#fff2cc",
                "max_color": "#c6efce",
            })

    # ================================================================
    # TAB 10: Revenue Trends (sparkline-style quarterly data)
    # ================================================================
    ws = wb.add_worksheet("Revenue Trends")
    ws.hide_gridlines(2)
    ws.set_column("A:A", 10)
    ws.set_column("B:B", 22)
    ws.set_column("C:J", 14)

    ws.merge_range("A1:J1", "Quarterly Revenue Trends", fmt_title)
    ws.merge_range("A2:J2", "Last 8 quarters of revenue by company", fmt_subtitle)

    row = 3
    for tier_name, stocks in all_tiers.items():
        ws.merge_range(row, 0, row, 9, tier_short.get(tier_name, tier_name), fmt_section)
        row += 1

        for s in stocks:
            qdates = s.get("quarterly_dates", [])
            qrevs = s.get("quarterly_revenue", [])
            qgm = s.get("quarterly_gm", [])
            if not qdates:
                continue

            ws.write(row, 0, s["ticker"], fmt_bold_text)
            ws.write(row, 1, "Revenue", fmt_text)
            for c, (d, rev) in enumerate(zip(qdates, qrevs)):
                ws.write(row - 1 if row > 4 else row, 2 + c, d, fmt_header) if c == 0 else None
                ws.write(row, 2 + c, rev / 1_000_000 if rev else 0, fmt_num)  # in $M
            row += 1
            ws.write(row, 1, "Gross Margin %", fmt_text)
            for c, gm in enumerate(qgm):
                ws.write(row, 2 + c, gm, fmt_pct_display)
            row += 1

            # Add sparkline if enough data
            if len(qrevs) >= 3:
                rev_data_range = xlsxwriter.utility.xl_range(row - 2, 2, row - 2, 1 + len(qrevs))
                ws.add_sparkline(row - 2, 1 + len(qrevs) + 1, {
                    "range": rev_data_range,
                    "type": "column",
                    "style": 36,
                })

            row += 1

    # ================================================================
    # TAB 11: Reference Stocks (Fingerprint Lab)
    # ================================================================
    ws = wb.add_worksheet("Fingerprint Reference")
    ws.hide_gridlines(2)
    ws.set_column("A:A", 8)
    ws.set_column("B:B", 28)
    ws.set_column("C:H", 16)

    ws.merge_range("A1:H1", "Fingerprint Reference Stocks", fmt_title)
    ws.merge_range("A2:H2",
                   "Historical winners at their pre-run inflection point — the benchmark for scoring",
                   fmt_subtitle)

    refs = load_reference_stocks()
    headers = [
        "Ticker", "Name", "Theme", "Year",
        "Rev Growth %", "Gross Margin %", "Insider %",
        "Dilution 3yr %",
    ]
    for c, h in enumerate(headers):
        ws.write(3, c, h, fmt_header_left if c < 3 else fmt_header)

    for r, ref in enumerate(refs):
        row = 4 + r
        ws.write(row, 0, ref["ticker"], fmt_bold_text)
        ws.write(row, 1, ref["name"], fmt_text)
        ws.write(row, 2, ref["theme"], fmt_text)
        ws.write(row, 3, ref["pre_run_year"], fmt_int)
        ws.write(row, 4, ref["pre_run_revenue_growth_pct"], fmt_pct_display)
        ws.write(row, 5, ref["pre_run_gross_margin_pct"], fmt_pct_display)
        ws.write(row, 6, ref["pre_run_insider_ownership_pct"], fmt_pct_display)
        ws.write(row, 7, ref["pre_run_dilution_3yr_pct"], fmt_pct_display)

    row = 4 + len(refs) + 1
    ideal = compute_ideal_ranges(refs)
    ws.merge_range(row, 0, row, 7, "Ideal Ranges (Min–Max Across Winners)", fmt_section)
    row += 1
    ws.write(row, 0, "", fmt_text)
    ws.write(row, 1, "Ideal Min", fmt_bold_text)
    ws.write(row, 4, ideal["revenue_growth_pct"][0], fmt_pct_display)
    ws.write(row, 5, ideal["gross_margin_pct"][0], fmt_pct_display)
    ws.write(row, 6, ideal["insider_ownership_pct"][0], fmt_pct_display)
    ws.write(row, 7, ideal["dilution_3yr_pct"][0], fmt_pct_display)
    row += 1
    ws.write(row, 1, "Ideal Max", fmt_bold_text)
    ws.write(row, 4, ideal["revenue_growth_pct"][1], fmt_pct_display)
    ws.write(row, 5, ideal["gross_margin_pct"][1], fmt_pct_display)
    ws.write(row, 6, ideal["insider_ownership_pct"][1], fmt_pct_display)
    ws.write(row, 7, ideal["dilution_3yr_pct"][1], fmt_pct_display)

    # ================================================================
    # TAB 12: Methodology
    # ================================================================
    ws = wb.add_worksheet("Methodology")
    ws.hide_gridlines(2)
    ws.set_column("A:A", 3)
    ws.set_column("B:B", 25)
    ws.set_column("C:C", 60)

    ws.merge_range("B1:C1", "Screening Methodology", fmt_title)

    sections = [
        ("Screening Universe", [
            "US-listed equities across three market cap tiers",
            "Nano Cap: $50M–$300M  |  Small Cap: $300M–$2B  |  Breakout: $2B–$15B",
            "Focus: Aerospace/Defense, Semiconductors, AI, New Energy, Gene Editing, Quantum, Photonics",
        ]),
        ("Hard Filters", [
            "Must be US-domiciled and actively trading on major exchange",
            "Excludes: Biotech/Pharma (unless gene editing), Oil & Gas, Warrants/Units/Preferred",
            "Must match industry whitelist OR description keywords in target themes",
        ]),
        ("Composite Score (60% weight)", [
            f"Revenue Growth YoY: {DEFAULT_WEIGHTS['revenue_growth']*100:.0f}% — Higher is better, >50% = max",
            f"Gross Margin: {DEFAULT_WEIGHTS['gross_margin']*100:.0f}% — >40% = max, proxy for pricing power",
            f"Dilution 3yr: {DEFAULT_WEIGHTS['dilution']*100:.0f}% — Lower is better, <5% = max",
            f"Insider Ownership: {DEFAULT_WEIGHTS['insider_ownership']*100:.0f}% — >10% = max",
            f"Revenue Acceleration: {DEFAULT_WEIGHTS['revenue_acceleration']*100:.0f}% — Growth rate increasing",
        ]),
        ("Fingerprint Score (40% weight)", [
            "Measures similarity to reference stocks (LITE, COHR, AAOI, KTOS, RKLB) at their pre-run inflection",
            "Compares revenue growth, gross margin, insider %, dilution, and market cap",
            "Inside the ideal range = 100; decays with distance from range",
        ]),
        ("Quality Floor", [
            "Revenue growth must be >5% YoY",
            "Gross margin must be >20%",
            "3-year dilution must be <30%",
        ]),
        ("Position Sizing Logic", [
            "Based on conviction level (High/Medium-High/Medium/Speculative)",
            "Nano caps get 0.6x multiplier; Breakouts get 1.2x multiplier",
            "Designed to sum to <100% with meaningful cash reserve",
        ]),
        ("Data Source", [
            "Financial Modeling Prep (FMP) API — all data points",
            "24-hour cache on all API responses",
            f"Report generated: {run_date}",
        ]),
    ]

    row = 3
    for section_title, items in sections:
        ws.write(row, 1, section_title, fmt_section)
        row += 1
        for item in items:
            ws.write(row, 2, item, fmt_text)
            row += 1
        row += 1

    # ================================================================
    # Finalize
    # ================================================================
    wb.close()
    print(f"\n  Workbook saved: {output_path}")
    print(f"  {len(all_stocks)} stocks across {len(all_tiers)} tiers")


# ── Main ──────────────────────────────────────────────────────────────

def main() -> None:
    if not FMP_API_KEY:
        print("Error: FMP_API_KEY not set in .env")
        sys.exit(1)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    output_dir = os.path.join(os.path.dirname(__file__), "output")
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, f"screener_{timestamp}.xlsx")

    print("Stock Screener — Excel Export")
    print("=" * 50)
    client = FMPClient(FMP_API_KEY)

    print("\n  Collecting data across all tiers...")
    all_tiers = collect_all_data(client)

    print("\n  Writing Excel workbook...")
    write_excel(all_tiers, output_path)

    print("\n  Done.")


if __name__ == "__main__":
    main()
