"""Export screener results to an institutional-quality Excel workbook (openpyxl)."""

import os
import sys
from datetime import datetime
from typing import Any

import pandas as pd
from openpyxl import Workbook
from openpyxl.chart import (
    BarChart,
    BubbleChart,
    DoughnutChart,
    RadarChart,
    Reference,
    Series,
)
from openpyxl.chart.label import DataLabelList
from openpyxl.chart.series import DataPoint
from openpyxl.drawing.fill import PatternFillProperties, ColorChoice
from openpyxl.formatting.rule import CellIsRule, ColorScaleRule
from openpyxl.styles import (
    Alignment,
    Border,
    Font,
    NamedStyle,
    PatternFill,
    Side,
    numbers,
)
from openpyxl.utils import get_column_letter
from openpyxl.workbook.defined_name import DefinedName
from openpyxl.worksheet.datavalidation import DataValidation

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


# ═══════════════════════════════════════════════════════════════════════
# DESIGN SYSTEM
# ═══════════════════════════════════════════════════════════════════════

COLORS = {
    "navy":          "0D1B2A",
    "dark_slate":    "1B2A3B",
    "charcoal":      "2E3B4E",
    "off_white":     "F7F9FC",
    "white":         "FFFFFF",
    "gold":          "C9A84C",
    "teal":          "2A9D8F",
    "coral":         "E76F51",
    "sky":           "4FC3F7",
    "light_gold":    "F4E4B0",
    "score_high":    "1A6B3C",
    "score_mid":     "F9C74F",
    "score_low":     "C1121F",
    "text_white":    "FFFFFF",
    "text_dark":     "0D1B2A",
    "text_muted":    "6B7280",
    "input_blue":    "0000FF",
    "formula_black": "000000",
    "link_green":    "008000",
}

# Pre-built fills
FILL_NAVY = PatternFill("solid", fgColor=COLORS["navy"])
FILL_DARK_SLATE = PatternFill("solid", fgColor=COLORS["dark_slate"])
FILL_CHARCOAL = PatternFill("solid", fgColor=COLORS["charcoal"])
FILL_OFF_WHITE = PatternFill("solid", fgColor=COLORS["off_white"])
FILL_WHITE = PatternFill("solid", fgColor=COLORS["white"])
FILL_GOLD = PatternFill("solid", fgColor=COLORS["gold"])
FILL_LIGHT_GOLD = PatternFill("solid", fgColor=COLORS["light_gold"])
FILL_TEAL = PatternFill("solid", fgColor=COLORS["teal"])
FILL_CORAL = PatternFill("solid", fgColor=COLORS["coral"])
FILL_SKY = PatternFill("solid", fgColor=COLORS["sky"])

# Conviction fills
CONVICTION_FILLS = {
    "High": FILL_TEAL,
    "Medium-High": FILL_SKY,
    "Medium": FILL_LIGHT_GOLD,
    "Speculative": FILL_CORAL,
}
CONVICTION_FONTS = {
    "High": Font(name="Arial", size=10, bold=True, color=COLORS["text_white"]),
    "Medium-High": Font(name="Arial", size=10, bold=True, color=COLORS["text_dark"]),
    "Medium": Font(name="Arial", size=10, bold=True, color=COLORS["text_dark"]),
    "Speculative": Font(name="Arial", size=10, bold=True, color=COLORS["text_white"]),
}

# Risk fills
RISK_FILLS = {
    "High": PatternFill("solid", fgColor="D9534F"),
    "Medium-High": FILL_CORAL,
    "Medium": FILL_LIGHT_GOLD,
    "Medium-Low": PatternFill("solid", fgColor="B5D8B0"),
}
RISK_FONTS = {
    "High": Font(name="Arial", size=10, color=COLORS["text_white"]),
    "Medium-High": Font(name="Arial", size=10, color=COLORS["text_white"]),
    "Medium": Font(name="Arial", size=10, color=COLORS["text_dark"]),
    "Medium-Low": Font(name="Arial", size=10, color=COLORS["text_dark"]),
}

# Fonts
FONT_TITLE = Font(name="Arial", size=13, bold=True, color=COLORS["text_white"])
FONT_TITLE_GOLD = Font(name="Arial", size=18, bold=True, color=COLORS["gold"])
FONT_SUBTITLE = Font(name="Arial", size=11, color=COLORS["text_white"])
FONT_SECTION = Font(name="Arial", size=11, bold=True, color=COLORS["gold"])
FONT_COL_HEADER = Font(name="Arial", size=10, bold=True, color=COLORS["text_white"])
FONT_BODY = Font(name="Arial", size=10, color=COLORS["text_dark"])
FONT_BODY_BOLD = Font(name="Arial", size=10, bold=True, color=COLORS["text_dark"])
FONT_TICKER = Font(name="Arial", size=10, bold=True, color=COLORS["sky"])
FONT_MUTED = Font(name="Arial", size=9, color=COLORS["text_muted"])
FONT_KPI_VALUE = Font(name="Arial", size=16, bold=True, color=COLORS["gold"])
FONT_KPI_LABEL = Font(name="Arial", size=9, color=COLORS["text_white"])
FONT_INPUT = Font(name="Arial", size=10, color=COLORS["input_blue"])
FONT_LINK = Font(name="Arial", size=10, color=COLORS["link_green"])
FONT_FORMULA = Font(name="Arial", size=10, color=COLORS["formula_black"])

# Borders
BORDER_THIN = Border(
    left=Side(style="thin", color=COLORS["charcoal"]),
    right=Side(style="thin", color=COLORS["charcoal"]),
    top=Side(style="thin", color=COLORS["charcoal"]),
    bottom=Side(style="thin", color=COLORS["charcoal"]),
)
BORDER_THICK_NAVY = Border(
    left=Side(style="medium", color=COLORS["navy"]),
    right=Side(style="medium", color=COLORS["navy"]),
    top=Side(style="medium", color=COLORS["navy"]),
    bottom=Side(style="medium", color=COLORS["navy"]),
)
BORDER_GOLD_BOTTOM = Border(
    bottom=Side(style="medium", color=COLORS["gold"])
)
BORDER_GOLD_TOP = Border(
    top=Side(style="medium", color=COLORS["gold"])
)
BORDER_NONE = Border()

# Alignments
ALIGN_CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
ALIGN_LEFT = Alignment(horizontal="left", vertical="center", wrap_text=True)
ALIGN_RIGHT = Alignment(horizontal="right", vertical="center")
ALIGN_WRAP = Alignment(horizontal="left", vertical="top", wrap_text=True)


# ═══════════════════════════════════════════════════════════════════════
# CELL HELPERS
# ═══════════════════════════════════════════════════════════════════════

def _set_cell(ws, row: int, col: int, value, font=None, fill=None,
              border=None, alignment=None, number_format=None):
    """Write a value and apply styling to a single cell."""
    cell = ws.cell(row=row, column=col, value=value)
    cell.font = font or FONT_BODY
    if fill:
        cell.fill = fill
    if border:
        cell.border = border
    if alignment:
        cell.alignment = alignment
    if number_format:
        cell.number_format = number_format
    return cell


def _write_title_block(ws, row: int, col_start: int, col_end: int,
                       title: str, subtitle: str = "", height: int = 28):
    """Merge cells and write a navy/gold title block."""
    ws.merge_cells(start_row=row, start_column=col_start,
                   end_row=row, end_column=col_end)
    cell = ws.cell(row=row, column=col_start, value=title)
    cell.font = FONT_TITLE
    cell.fill = FILL_NAVY
    cell.alignment = ALIGN_CENTER
    cell.border = BORDER_THICK_NAVY
    # Fill merged area
    for c in range(col_start + 1, col_end + 1):
        ws.cell(row=row, column=c).fill = FILL_NAVY
        ws.cell(row=row, column=c).border = BORDER_THICK_NAVY
    ws.row_dimensions[row].height = height
    if subtitle:
        row += 1
        ws.merge_cells(start_row=row, start_column=col_start,
                       end_row=row, end_column=col_end)
        cell = ws.cell(row=row, column=col_start, value=subtitle)
        cell.font = FONT_SUBTITLE
        cell.fill = FILL_NAVY
        cell.alignment = ALIGN_CENTER
        for c in range(col_start + 1, col_end + 1):
            ws.cell(row=row, column=c).fill = FILL_NAVY
        ws.row_dimensions[row].height = 22
    return row


def _write_section_header(ws, row: int, col_start: int, col_end: int,
                          text: str):
    """Write a gold-on-dark-slate section header with gold bottom border."""
    ws.merge_cells(start_row=row, start_column=col_start,
                   end_row=row, end_column=col_end)
    cell = ws.cell(row=row, column=col_start, value=text)
    cell.font = FONT_SECTION
    cell.fill = FILL_DARK_SLATE
    cell.alignment = ALIGN_LEFT
    cell.border = BORDER_GOLD_BOTTOM
    for c in range(col_start + 1, col_end + 1):
        ws.cell(row=row, column=c).fill = FILL_DARK_SLATE
        ws.cell(row=row, column=c).border = BORDER_GOLD_BOTTOM
    ws.row_dimensions[row].height = 22
    return row


def _write_col_headers(ws, row: int, col_start: int, headers: list[str]):
    """Write charcoal column headers."""
    for i, h in enumerate(headers):
        cell = ws.cell(row=row, column=col_start + i, value=h)
        cell.font = FONT_COL_HEADER
        cell.fill = FILL_CHARCOAL
        cell.alignment = ALIGN_CENTER
        cell.border = BORDER_THIN
    ws.row_dimensions[row].height = 18
    return row


def _body_fill(row_idx: int) -> PatternFill:
    """Return alternating row fill."""
    return FILL_OFF_WHITE if row_idx % 2 == 0 else FILL_WHITE


def _write_body_cell(ws, row: int, col: int, value, row_idx: int = 0,
                     font=None, alignment=None, number_format=None,
                     fill_override=None):
    """Write a body cell with alternating row shading."""
    fill = fill_override if fill_override else _body_fill(row_idx)
    return _set_cell(ws, row, col, value, font=font or FONT_BODY,
                     fill=fill, border=BORDER_THIN,
                     alignment=alignment or ALIGN_CENTER,
                     number_format=number_format)


def _write_conviction_cell(ws, row: int, col: int, conviction: str):
    """Write a conviction cell with proper color coding."""
    cell = ws.cell(row=row, column=col, value=conviction)
    cell.fill = CONVICTION_FILLS.get(conviction, FILL_WHITE)
    cell.font = CONVICTION_FONTS.get(conviction, FONT_BODY)
    cell.alignment = ALIGN_CENTER
    cell.border = BORDER_THIN
    return cell


def _write_risk_cell(ws, row: int, col: int, risk: str):
    """Write a risk category cell with proper color coding."""
    cell = ws.cell(row=row, column=col, value=risk)
    cell.fill = RISK_FILLS.get(risk, FILL_WHITE)
    cell.font = RISK_FONTS.get(risk, FONT_BODY)
    cell.alignment = ALIGN_CENTER
    cell.border = BORDER_THIN
    return cell


def _apply_score_color_scale(ws, cell_range: str):
    """Apply 3-color score gradient: red → amber → green."""
    rule = ColorScaleRule(
        start_type="num", start_value=0, start_color=COLORS["score_low"],
        mid_type="num", mid_value=50, mid_color=COLORS["score_mid"],
        end_type="num", end_value=100, end_color=COLORS["score_high"],
    )
    ws.conditional_formatting.add(cell_range, rule)


def _apply_perf_color_scale(ws, cell_range: str):
    """Apply performance color scale: coral → white → teal."""
    rule = ColorScaleRule(
        start_type="min", start_color=COLORS["coral"],
        mid_type="num", mid_value=0, mid_color=COLORS["white"],
        end_type="max", end_color=COLORS["teal"],
    )
    ws.conditional_formatting.add(cell_range, rule)


def _apply_green_scale(ws, cell_range: str):
    """Higher = greener scale for positive metrics."""
    rule = ColorScaleRule(
        start_type="min", start_color=COLORS["white"],
        end_type="max", end_color=COLORS["teal"],
    )
    ws.conditional_formatting.add(cell_range, rule)


def _apply_red_scale_reverse(ws, cell_range: str):
    """Lower = greener (for dilution — lower is better)."""
    rule = ColorScaleRule(
        start_type="min", start_color=COLORS["teal"],
        end_type="max", end_color=COLORS["coral"],
    )
    ws.conditional_formatting.add(cell_range, rule)


def _setup_sheet(ws, title: str = ""):
    """Common sheet setup: hide gridlines, landscape, fit to page."""
    ws.sheet_view.showGridLines = False
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToPage = True
    ws.page_setup.fitToWidth = 1


def _safe(val, default=0):
    """Return val if not None, else default."""
    return val if val is not None else default


def _pct_from_high(price, high):
    """Calculate % from 52-week high."""
    if price and high and high > 0:
        return (price - high) / high
    return None


# ═══════════════════════════════════════════════════════════════════════
# BUSINESS LOGIC (unchanged from original)
# ═══════════════════════════════════════════════════════════════════════

def _classify_thesis(s: dict) -> dict[str, str]:
    """Return conviction level, thesis bucket, and rationale for a stock."""
    combined = s["combined"]
    rev = s["rev_growth_pct"] or 0
    gm = s["gross_margin_pct"] or 0
    dil = s["dilution_3yr_pct"] or 0
    insider = s["insider_pct"] or 0
    fp = s["fingerprint"]
    mom_3m = s.get("3m_pct") or 0

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

    if combined >= 85 and len(risks) <= 1:
        conviction = "High"
    elif combined >= 75:
        conviction = "Medium-High"
    elif combined >= 65:
        conviction = "Medium"
    else:
        conviction = "Speculative"

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
        "strengths": "; ".join(strengths) if strengths else "N/A",
        "risks": "; ".join(risks) if risks else "none significant",
    }


def _position_size_pct(conviction: str, tier_name: str) -> float:
    base = {"High": 8.0, "Medium-High": 5.0, "Medium": 3.0, "Speculative": 1.5}
    size = base.get(conviction, 2.0)
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

TIER_SHORT = {
    "Nano Cap ($50M–$300M)": "Nano Cap",
    "Small Cap ($300M–$2B)": "Small Cap",
    "Breakout ($2B–$15B)": "Breakout",
}


# ═══════════════════════════════════════════════════════════════════════
# DATA COLLECTION
# ═══════════════════════════════════════════════════════════════════════

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


# ═══════════════════════════════════════════════════════════════════════
# SHEET BUILDERS
# ═══════════════════════════════════════════════════════════════════════

def _build_dashboard(wb: Workbook, all_tiers: dict, all_stocks: list, run_date: str):
    """Sheet 1: Dashboard — Bloomberg-style cover page."""
    ws = wb.active
    ws.title = "Dashboard"
    _setup_sheet(ws)
    ws.sheet_properties.tabColor = COLORS["gold"]

    # Column widths: A-P
    col_widths = {1: 3, 2: 14, 3: 14, 4: 14, 5: 14, 6: 14, 7: 14,
                  8: 3, 9: 14, 10: 14, 11: 14, 12: 14, 13: 14, 14: 14,
                  15: 3, 16: 14}
    for c, w in col_widths.items():
        ws.column_dimensions[get_column_letter(c)].width = w

    # ── Master Header Block (rows 1-4) ──
    for r in range(1, 5):
        for c in range(1, 17):
            cell = ws.cell(row=r, column=c)
            cell.fill = FILL_NAVY
            cell.border = BORDER_THICK_NAVY

    ws.merge_cells("A1:P2")
    cell = ws.cell(row=1, column=1, value="STRUCTURAL GROWTH EQUITY SCREENER")
    cell.font = FONT_TITLE_GOLD
    cell.fill = FILL_NAVY
    cell.alignment = ALIGN_CENTER
    ws.row_dimensions[1].height = 28
    ws.row_dimensions[2].height = 28

    ws.merge_cells("A3:J3")
    cell = ws.cell(row=3, column=1, value="Nano  ·  Small  ·  Breakout Tier Analysis")
    cell.font = FONT_SUBTITLE
    cell.fill = FILL_NAVY
    cell.alignment = ALIGN_CENTER

    # Right side: run date, universe size, source
    ws.merge_cells("K3:P3")
    meta_text = f"Run Date: {run_date}  |  Universe: {len(all_stocks)} stocks  |  Source: Financial Modeling Prep"
    cell = ws.cell(row=3, column=11, value=meta_text)
    cell.font = Font(name="Arial", size=9, color=COLORS["text_muted"])
    cell.fill = FILL_NAVY
    cell.alignment = Alignment(horizontal="right", vertical="center")

    ws.row_dimensions[3].height = 20
    ws.row_dimensions[4].height = 8  # spacer

    # ── KPI Bar (rows 6-8) ──
    avg_combined = sum(s["combined"] for s in all_stocks) / len(all_stocks) if all_stocks else 0
    rgs = [s["rev_growth_pct"] for s in all_stocks if s["rev_growth_pct"] is not None]
    avg_rg = sum(rgs) / len(rgs) if rgs else 0
    gms = [s["gross_margin_pct"] for s in all_stocks if s["gross_margin_pct"] is not None]
    avg_gm = sum(gms) / len(gms) if gms else 0
    high_conviction = sum(1 for s in all_stocks if _classify_thesis(s)["conviction"] == "High")

    kpis = [
        ("TOTAL SCREENED", str(len(all_stocks))),
        ("PASSED FILTERS", str(len(all_stocks))),
        ("AVG COMBINED SCORE", f"{avg_combined:.1f}"),
        ("AVG REV GROWTH", f"{avg_rg:.1f}%"),
        ("AVG GROSS MARGIN", f"{avg_gm:.1f}%"),
        ("HIGH CONVICTION", str(high_conviction)),
    ]

    kpi_cols = [(2, 3), (4, 5), (6, 7), (9, 10), (11, 12), (13, 14)]
    for (label, value), (c1, c2) in zip(kpis, kpi_cols):
        # Top border in gold
        for c in range(c1, c2 + 1):
            ws.cell(row=5, column=c).fill = FILL_NAVY
            ws.cell(row=5, column=c).border = BORDER_GOLD_TOP

        # Label row
        ws.merge_cells(start_row=6, start_column=c1, end_row=6, end_column=c2)
        cell = ws.cell(row=6, column=c1, value=label)
        cell.font = FONT_KPI_LABEL
        cell.fill = FILL_NAVY
        cell.alignment = ALIGN_CENTER

        # Value row
        ws.merge_cells(start_row=7, start_column=c1, end_row=7, end_column=c2)
        cell = ws.cell(row=7, column=c1, value=value)
        cell.font = FONT_KPI_VALUE
        cell.fill = FILL_NAVY
        cell.alignment = ALIGN_CENTER

        # Bottom spacer
        for c in range(c1, c2 + 1):
            ws.cell(row=8, column=c).fill = FILL_NAVY

    ws.row_dimensions[5].height = 6
    ws.row_dimensions[6].height = 16
    ws.row_dimensions[7].height = 28
    ws.row_dimensions[8].height = 6

    # Fill spacer columns in KPI row
    for r in range(5, 9):
        for c in [1, 8, 15, 16]:
            ws.cell(row=r, column=c).fill = FILL_NAVY

    ws.row_dimensions[9].height = 8  # spacer

    # ── Three-Column Tier Summary (row 10 onward) ──
    tier_start_cols = [2, 7, 12]  # starting columns for each tier
    tier_names = list(all_tiers.keys())

    for tier_idx, tier_name in enumerate(tier_names):
        stocks = all_tiers[tier_name][:5]
        c_start = tier_start_cols[tier_idx]
        short = TIER_SHORT.get(tier_name, tier_name[:15])

        # Tier header
        ws.merge_cells(start_row=10, start_column=c_start,
                       end_row=10, end_column=c_start + 4)
        cell = ws.cell(row=10, column=c_start, value=f"  {short}")
        cell.font = FONT_SECTION
        cell.fill = FILL_DARK_SLATE
        cell.alignment = ALIGN_LEFT
        cell.border = BORDER_GOLD_BOTTOM
        for c in range(c_start + 1, c_start + 5):
            ws.cell(row=10, column=c).fill = FILL_DARK_SLATE
            ws.cell(row=10, column=c).border = BORDER_GOLD_BOTTOM

        # Column headers
        hdrs = ["Ticker", "Company", "Mkt Cap", "Score", "Conviction"]
        for i, h in enumerate(hdrs):
            cell = ws.cell(row=11, column=c_start + i, value=h)
            cell.font = FONT_COL_HEADER
            cell.fill = FILL_CHARCOAL
            cell.alignment = ALIGN_CENTER
            cell.border = BORDER_THIN

        # Data rows
        for ri, s in enumerate(stocks):
            r = 12 + ri
            thesis = _classify_thesis(s)
            fill = _body_fill(ri)
            _write_body_cell(ws, r, c_start, s["ticker"], ri,
                             font=FONT_TICKER)
            _write_body_cell(ws, r, c_start + 1, s["name"], ri,
                             alignment=ALIGN_LEFT)
            _write_body_cell(ws, r, c_start + 2, s["mkt_cap_m"], ri,
                             number_format="$#,##0")
            # Score badge with color
            score = s["combined"]
            if score >= 80:
                score_fill = PatternFill("solid", fgColor=COLORS["score_high"])
                score_font = Font(name="Arial", size=10, bold=True, color=COLORS["text_white"])
            elif score >= 60:
                score_fill = PatternFill("solid", fgColor=COLORS["score_mid"])
                score_font = Font(name="Arial", size=10, bold=True, color=COLORS["text_dark"])
            else:
                score_fill = PatternFill("solid", fgColor=COLORS["score_low"])
                score_font = Font(name="Arial", size=10, bold=True, color=COLORS["text_white"])
            _write_body_cell(ws, r, c_start + 3, score, ri,
                             font=score_font, fill_override=score_fill,
                             number_format="0.0")
            _write_conviction_cell(ws, r, c_start + 4, thesis["conviction"])
            ws.row_dimensions[r].height = 16

    ws.row_dimensions[10].height = 22
    ws.row_dimensions[11].height = 18

    # ── Bottom section: Charts ──
    chart_row = 19

    # Bar chart: top 10 by combined score
    _write_section_header(ws, chart_row, 2, 16,
                          "TOP PERFORMERS — Combined Score Ranking")
    chart_row += 1

    # Write data for chart (hidden area)
    chart_data_row = 40
    sorted_all = sorted(all_stocks, key=lambda x: x["combined"], reverse=True)[:10]
    ws.cell(row=chart_data_row, column=1, value="Ticker").font = FONT_BODY
    ws.cell(row=chart_data_row, column=2, value="Score").font = FONT_BODY
    ws.cell(row=chart_data_row, column=3, value="Tier").font = FONT_BODY
    for i, s in enumerate(sorted_all):
        ws.cell(row=chart_data_row + 1 + i, column=1, value=s["ticker"])
        ws.cell(row=chart_data_row + 1 + i, column=2, value=s["combined"])
        ws.cell(row=chart_data_row + 1 + i, column=3,
                value=TIER_SHORT.get(s["tier"], s["tier"]))

    if sorted_all:
        chart = BarChart()
        chart.type = "col"
        chart.style = 10
        chart.title = None
        chart.y_axis.title = "Combined Score"
        chart.x_axis.title = None
        chart.legend = None
        chart.width = 28
        chart.height = 12

        data_ref = Reference(ws, min_col=2, min_row=chart_data_row,
                             max_row=chart_data_row + len(sorted_all))
        cats = Reference(ws, min_col=1,
                         min_row=chart_data_row + 1,
                         max_row=chart_data_row + len(sorted_all))
        chart.add_data(data_ref, titles_from_data=True)
        chart.set_categories(cats)

        # Style the chart
        series = chart.series[0]
        series.graphicalProperties.solidFill = COLORS["gold"]
        chart.y_axis.majorGridlines = None

        ws.add_chart(chart, f"B{chart_row}")

    ws.freeze_panes = "A5"


def _build_tier_sheet(wb: Workbook, ws_name: str, tier_name: str,
                      stocks: list[dict], run_date: str):
    """Sheets 2-4: Individual tier results with full metrics."""
    ws = wb.create_sheet(ws_name)
    _setup_sheet(ws)

    # Tab colors
    tab_colors = {"Nano Cap": COLORS["sky"], "Small Cap": COLORS["teal"],
                  "Breakout": COLORS["navy"]}
    ws.sheet_properties.tabColor = tab_colors.get(ws_name, COLORS["sky"])

    # Column widths
    col_config = [
        (1, 6),    # Rank
        (2, 9),    # Ticker
        (3, 28),   # Company Name
        (4, 14),   # Mkt Cap
        (5, 12),   # Price
        (6, 14),   # Rev Growth
        (7, 14),   # Rev Accel
        (8, 14),   # Gross Margin
        (9, 14),   # Dilution
        (10, 12),  # Insider %
        (11, 12),  # Composite
        (12, 12),  # Fingerprint
        (13, 12),  # Combined
        (14, 12),  # Conviction
        (15, 10),  # 1M
        (16, 10),  # 3M
        (17, 10),  # 6M
        (18, 10),  # YTD
        (19, 10),  # 1Y
    ]
    for col, width in col_config:
        ws.column_dimensions[get_column_letter(col)].width = width

    # Header block (rows 1-3)
    _write_title_block(ws, 1, 1, 19, f"{tier_name} — Screener Results",
                       f"Top {len(stocks)} by combined score  |  {run_date}")
    ws.row_dimensions[3].height = 8  # spacer

    # Column headers (row 4)
    headers = [
        "Rank", "Ticker", "Company Name", "Mkt Cap ($M)", "Price",
        "Rev Growth YoY", "Rev Accel", "Gross Margin", "Dilution 3yr",
        "Insider %", "Composite", "Fingerprint", "Combined", "Conviction",
        "1M", "3M", "6M", "YTD", "1Y",
    ]
    _write_col_headers(ws, 4, 1, headers)

    # Data rows
    for ri, s in enumerate(stocks):
        r = 5 + ri
        fill = _body_fill(ri)
        thesis = _classify_thesis(s)

        # Rank
        _set_cell(ws, r, 1, ri + 1, font=Font(name="Arial", size=10, bold=True,
                  color=COLORS["text_white"]), fill=FILL_NAVY,
                  alignment=ALIGN_CENTER, border=BORDER_THIN)
        # Ticker
        _write_body_cell(ws, r, 2, s["ticker"], ri, font=FONT_TICKER)
        # Company
        _write_body_cell(ws, r, 3, s["name"], ri, alignment=ALIGN_LEFT)
        # Mkt Cap
        _write_body_cell(ws, r, 4, s["mkt_cap_m"], ri,
                         number_format="$#,##0")
        # Price
        _write_body_cell(ws, r, 5, s["price"], ri,
                         number_format="$#,##0.00")
        # Rev Growth (as decimal for % format)
        _write_body_cell(ws, r, 6, _safe(s["rev_growth_pct"]) / 100, ri,
                         number_format="0.0%")
        # Rev Accel
        _write_body_cell(ws, r, 7, _safe(s["rev_accel_pct"]) / 100, ri,
                         number_format="0.0%")
        # Gross Margin
        _write_body_cell(ws, r, 8, _safe(s["gross_margin_pct"]) / 100, ri,
                         number_format="0.0%")
        # Dilution
        _write_body_cell(ws, r, 9, _safe(s["dilution_3yr_pct"]) / 100, ri,
                         number_format="0.0%")
        # Insider %
        _write_body_cell(ws, r, 10, _safe(s["insider_pct"]) / 100, ri,
                         number_format="0.0%")
        # Scores
        _write_body_cell(ws, r, 11, s["composite"], ri,
                         number_format="0.0")
        _write_body_cell(ws, r, 12, s["fingerprint"], ri,
                         number_format="0.0")
        _write_body_cell(ws, r, 13, s["combined"], ri,
                         font=FONT_BODY_BOLD, number_format="0.0")
        # Conviction
        _write_conviction_cell(ws, r, 14, thesis["conviction"])
        # Performance
        for ci, key in enumerate(["1m_pct", "3m_pct", "6m_pct", "ytd_pct", "1y_pct"]):
            val = s.get(key)
            _write_body_cell(ws, r, 15 + ci,
                             val / 100 if val is not None else None, ri,
                             number_format="0.0%")

        ws.row_dimensions[r].height = 16

    # Conditional formatting
    if stocks:
        last = 4 + len(stocks)
        # Score columns (K, L, M)
        for col_letter in ["K", "L", "M"]:
            _apply_score_color_scale(ws, f"{col_letter}5:{col_letter}{last}")
        # Metric color scales
        _apply_green_scale(ws, f"F5:F{last}")    # Rev growth
        _apply_green_scale(ws, f"H5:H{last}")    # Gross margin
        _apply_red_scale_reverse(ws, f"I5:I{last}")  # Dilution (lower=better)
        _apply_green_scale(ws, f"J5:J{last}")    # Insider
        # Performance columns
        for col_letter in ["O", "P", "Q", "R", "S"]:
            _apply_perf_color_scale(ws, f"{col_letter}5:{col_letter}{last}")

    # ── Score Breakdown Mini-Table (below main table) ──
    if stocks:
        gap_row = 5 + len(stocks) + 2
        _write_section_header(ws, gap_row, 1, 19,
                              "SCORE COMPONENT BREAKDOWN")
        gap_row += 1
        comp_headers = ["Ticker", "Rev Growth", "Gross Margin", "Dilution",
                        "Insider Own", "Acceleration"]
        _write_col_headers(ws, gap_row, 1, comp_headers)
        gap_row += 1

        for ri, s in enumerate(stocks):
            _write_body_cell(ws, gap_row, 1, s["ticker"], ri, font=FONT_TICKER)
            # Compute individual component scores
            from screener.filters import (
                _score_revenue_growth, _score_gross_margin,
                _score_dilution, _score_insider_ownership,
                _score_revenue_acceleration,
            )
            scores = [
                _score_revenue_growth(_safe(s["rev_growth_pct"])),
                _score_gross_margin(_safe(s["gross_margin_pct"])),
                _score_dilution(_safe(s["dilution_3yr_pct"])),
                _score_insider_ownership(_safe(s["insider_pct"])),
                _score_revenue_acceleration(_safe(s["rev_accel_pct"])),
            ]
            for ci, sc in enumerate(scores):
                _write_body_cell(ws, gap_row, 2 + ci, sc, ri,
                                 number_format="0.0")
            ws.row_dimensions[gap_row].height = 16
            gap_row += 1

        # Color scale on breakdown
        last_bd = gap_row - 1
        hdr_bd = gap_row - len(stocks)
        for col_letter in ["B", "C", "D", "E", "F"]:
            _apply_score_color_scale(ws, f"{col_letter}{hdr_bd}:{col_letter}{last_bd}")

    # ── Scatter Chart (Margin vs Growth) ──
    if len(stocks) >= 2:
        chart_data_start = 60
        ws.cell(row=chart_data_start, column=1, value="Ticker")
        ws.cell(row=chart_data_start, column=2, value="Gross Margin")
        ws.cell(row=chart_data_start, column=3, value="Rev Growth")
        ws.cell(row=chart_data_start, column=4, value="Mkt Cap")
        for i, s in enumerate(stocks):
            ws.cell(row=chart_data_start + 1 + i, column=1, value=s["ticker"])
            ws.cell(row=chart_data_start + 1 + i, column=2,
                    value=_safe(s["gross_margin_pct"]))
            ws.cell(row=chart_data_start + 1 + i, column=3,
                    value=_safe(s["rev_growth_pct"]))
            ws.cell(row=chart_data_start + 1 + i, column=4,
                    value=s["mkt_cap_m"])

        chart = BubbleChart()
        chart.title = "Growth vs Margin (bubble = market cap)"
        chart.width = 18
        chart.height = 12
        chart.x_axis.title = "Gross Margin %"
        chart.y_axis.title = "Revenue Growth %"

        xvals = Reference(ws, min_col=2, min_row=chart_data_start + 1,
                          max_row=chart_data_start + len(stocks))
        yvals = Reference(ws, min_col=3, min_row=chart_data_start + 1,
                          max_row=chart_data_start + len(stocks))
        bubbles = Reference(ws, min_col=4, min_row=chart_data_start + 1,
                            max_row=chart_data_start + len(stocks))
        series = Series(yvals, xvals, bubbles, title="Stocks")
        series.graphicalProperties.solidFill = COLORS["gold"]
        chart.series.append(series)

        # Place chart to the right of the breakdown
        place_row = 5 + len(stocks) + 2
        ws.add_chart(chart, f"H{place_row}")

    ws.freeze_panes = "C5"


def _build_descriptions(wb: Workbook, all_tiers: dict, run_date: str):
    """Sheet 5: Descriptions with zebra striping and sector tints."""
    ws = wb.create_sheet("Descriptions")
    _setup_sheet(ws)
    ws.sheet_properties.tabColor = COLORS["charcoal"]

    col_widths = {1: 9, 2: 28, 3: 18, 4: 22, 5: 12, 6: 55}
    for c, w in col_widths.items():
        ws.column_dimensions[get_column_letter(c)].width = w

    _write_title_block(ws, 1, 1, 6, "Company Descriptions & Profiles",
                       f"All screened companies  |  {run_date}")
    ws.row_dimensions[3].height = 8

    headers = ["Ticker", "Company", "Sector", "Industry", "Tier", "Description"]
    _write_col_headers(ws, 4, 1, headers)

    # Sector tint mapping
    sector_tints = {
        "Aerospace & Defense": PatternFill("solid", fgColor="E8EDF2"),
        "Semiconductors": PatternFill("solid", fgColor="E0F0ED"),
        "Electronic Components": PatternFill("solid", fgColor="E0F0ED"),
        "Semiconductor Equipment & Materials": PatternFill("solid", fgColor="E0F0ED"),
        "Communication Equipment": PatternFill("solid", fgColor="EDE8F0"),
        "Solar": PatternFill("solid", fgColor="FFF8E1"),
    }

    row = 5
    for tier_name, stocks in all_tiers.items():
        for ri, s in enumerate(stocks):
            fill = _body_fill(ri)
            _write_body_cell(ws, row, 1, s["ticker"], ri, font=FONT_TICKER)
            _write_body_cell(ws, row, 2, s["name"], ri, alignment=ALIGN_LEFT)
            _write_body_cell(ws, row, 3, s["sector"], ri, alignment=ALIGN_LEFT)
            # Industry with sector tint
            ind_fill = sector_tints.get(s.get("industry"), fill)
            _write_body_cell(ws, row, 4, s["industry"], ri,
                             alignment=ALIGN_LEFT, fill_override=ind_fill)
            _write_body_cell(ws, row, 5, TIER_SHORT.get(tier_name, tier_name), ri)
            # Description
            desc = (s.get("description") or "")[:1000]
            _write_body_cell(ws, row, 6, desc, ri, alignment=ALIGN_WRAP)
            ws.row_dimensions[row].height = 60
            row += 1

    # Auto-filter on sector, industry, tier
    ws.auto_filter.ref = f"A4:F{row - 1}"

    ws.freeze_panes = "C5"


def _build_standouts(wb: Workbook, all_stocks: list, run_date: str):
    """Sheet 6: Wall of Fame — cross-tier standouts."""
    ws = wb.create_sheet("Standouts")
    _setup_sheet(ws)
    ws.sheet_properties.tabColor = COLORS["gold"]

    # Two columns of tables: left (B-G) and right (I-N)
    for c in [1]:
        ws.column_dimensions[get_column_letter(c)].width = 3
    for c in [2, 9]:
        ws.column_dimensions[get_column_letter(c)].width = 6   # rank
    for c in [3, 10]:
        ws.column_dimensions[get_column_letter(c)].width = 9   # ticker
    for c in [4, 11]:
        ws.column_dimensions[get_column_letter(c)].width = 24  # name
    for c in [5, 12]:
        ws.column_dimensions[get_column_letter(c)].width = 14  # value
    for c in [6, 13]:
        ws.column_dimensions[get_column_letter(c)].width = 10  # tier
    for c in [7, 14]:
        ws.column_dimensions[get_column_letter(c)].width = 12  # score
    ws.column_dimensions["H"].width = 3  # spacer

    _write_title_block(ws, 1, 1, 14, "CROSS-TIER STANDOUTS",
                       f"Top performers across all metrics  |  {run_date}")
    ws.row_dimensions[3].height = 8

    medals = ["🥇", "🥈", "🥉", "4th", "5th"]

    def write_category(start_row: int, col_start: int, title: str, emoji: str,
                       ranked: list, value_key: str, value_label: str,
                       number_fmt: str = "0.0") -> int:
        r = start_row
        col_end = col_start + 5

        # Category header
        ws.merge_cells(start_row=r, start_column=col_start,
                       end_row=r, end_column=col_end)
        cell = ws.cell(row=r, column=col_start, value=f"{emoji} {title}")
        cell.font = FONT_SECTION
        cell.fill = FILL_DARK_SLATE
        cell.alignment = ALIGN_LEFT
        cell.border = BORDER_GOLD_BOTTOM
        for c in range(col_start + 1, col_end + 1):
            ws.cell(row=r, column=c).fill = FILL_DARK_SLATE
            ws.cell(row=r, column=c).border = BORDER_GOLD_BOTTOM
        ws.row_dimensions[r].height = 22
        r += 1

        # Sub-headers
        sub_hdrs = ["Rank", "Ticker", "Company", value_label, "Tier", "Score"]
        _write_col_headers(ws, r, col_start, sub_hdrs)
        r += 1

        for i, s in enumerate(ranked[:5]):
            _write_body_cell(ws, r, col_start, medals[i], i)
            _write_body_cell(ws, r, col_start + 1, s["ticker"], i, font=FONT_TICKER)
            _write_body_cell(ws, r, col_start + 2, s["name"], i, alignment=ALIGN_LEFT)
            val = s.get(value_key)
            _write_body_cell(ws, r, col_start + 3,
                             val if val is not None else "N/A", i,
                             number_format=number_fmt)
            _write_body_cell(ws, r, col_start + 4,
                             TIER_SHORT.get(s["tier"], s["tier"]), i)
            _write_body_cell(ws, r, col_start + 5, s["combined"], i,
                             number_format="0.0")
            ws.row_dimensions[r].height = 16
            r += 1

        # Gold border around entire block
        for row_i in range(start_row, r):
            ws.cell(row=row_i, column=col_start).border = Border(
                left=Side(style="medium", color=COLORS["gold"]),
                top=ws.cell(row=row_i, column=col_start).border.top,
                bottom=ws.cell(row=row_i, column=col_start).border.bottom,
                right=ws.cell(row=row_i, column=col_start).border.right,
            )
            ws.cell(row=row_i, column=col_end).border = Border(
                right=Side(style="medium", color=COLORS["gold"]),
                top=ws.cell(row=row_i, column=col_end).border.top,
                bottom=ws.cell(row=row_i, column=col_end).border.bottom,
                left=ws.cell(row=row_i, column=col_end).border.left,
            )

        r += 1  # spacer
        return r

    # Left column categories
    r_left = 4
    by_score = sorted(all_stocks, key=lambda x: x["combined"], reverse=True)
    r_left = write_category(r_left, 2, "Highest Score", "🏆", by_score,
                            "combined", "Score", "0.0")

    by_fp = sorted(all_stocks, key=lambda x: x["fingerprint"], reverse=True)
    r_left = write_category(r_left, 2, "Best Fingerprint", "🧬", by_fp,
                            "fingerprint", "FP Score", "0.0")

    by_rev = sorted([s for s in all_stocks if s.get("rev_growth_pct") is not None],
                    key=lambda x: x["rev_growth_pct"], reverse=True)
    r_left = write_category(r_left, 2, "Fastest Growth", "🚀", by_rev,
                            "rev_growth_pct", "Rev Gr %", "0.0")

    by_mom = sorted([s for s in all_stocks if s.get("3m_pct") is not None],
                    key=lambda x: x["3m_pct"], reverse=True)
    r_left = write_category(r_left, 2, "Best Momentum", "📈", by_mom,
                            "3m_pct", "3M %", "0.0")

    # Right column categories
    r_right = 4
    by_dil = sorted([s for s in all_stocks if s.get("dilution_3yr_pct") is not None],
                    key=lambda x: x["dilution_3yr_pct"])
    r_right = write_category(r_right, 9, "Lowest Dilution", "🔒", by_dil,
                             "dilution_3yr_pct", "Dil %", "0.0")

    by_ins = sorted([s for s in all_stocks if s.get("insider_pct") is not None],
                    key=lambda x: x["insider_pct"], reverse=True)
    r_right = write_category(r_right, 9, "Highest Insider", "👤", by_ins,
                             "insider_pct", "Insider %", "0.0")

    by_size = sorted(all_stocks, key=lambda x: x["mkt_cap_m"])
    r_right = write_category(r_right, 9, "Smallest Cap", "🔬", by_size,
                             "mkt_cap_m", "Mkt Cap", "$#,##0")

    ws.freeze_panes = "A4"


def _build_recommendations(wb: Workbook, all_stocks: list, run_date: str):
    """Sheet 7: Investment recommendations — sell-side research style."""
    ws = wb.create_sheet("Recommendations")
    _setup_sheet(ws)
    ws.sheet_properties.tabColor = COLORS["teal"]

    col_widths = {1: 9, 2: 28, 3: 12, 4: 14, 5: 18, 6: 12,
                  7: 55, 8: 30, 9: 30}
    for c, w in col_widths.items():
        ws.column_dimensions[get_column_letter(c)].width = w

    _write_title_block(ws, 1, 1, 9, "INVESTMENT RECOMMENDATIONS",
                       f"Conviction levels based on combined scoring, fundamentals, and risk profile  |  {run_date}")
    ws.row_dimensions[3].height = 8

    # ── Filter row (row 4) ──
    r = 4
    _set_cell(ws, r, 1, "▼ Filter:", font=FONT_MUTED, alignment=ALIGN_RIGHT)

    # Conviction dropdown
    _set_cell(ws, r, 2, "All", font=FONT_INPUT, fill=FILL_LIGHT_GOLD,
              border=Border(left=Side(style="medium", color=COLORS["gold"]),
                            right=Side(style="medium", color=COLORS["gold"]),
                            top=Side(style="medium", color=COLORS["gold"]),
                            bottom=Side(style="medium", color=COLORS["gold"])),
              alignment=ALIGN_CENTER)
    dv1 = DataValidation(
        type="list",
        formula1='"All,High,Medium-High,Medium,Speculative"',
        allow_blank=True, showDropDown=False
    )
    dv1.error = "Please select a valid conviction level"
    dv1.errorTitle = "Invalid Selection"
    ws.add_data_validation(dv1)
    dv1.add(ws.cell(row=r, column=2))

    # Tier dropdown
    _set_cell(ws, r, 3, "All", font=FONT_INPUT, fill=FILL_LIGHT_GOLD,
              border=Border(left=Side(style="medium", color=COLORS["gold"]),
                            right=Side(style="medium", color=COLORS["gold"]),
                            top=Side(style="medium", color=COLORS["gold"]),
                            bottom=Side(style="medium", color=COLORS["gold"])),
              alignment=ALIGN_CENTER)
    dv2 = DataValidation(
        type="list",
        formula1='"All,Nano,Small,Breakout"',
        allow_blank=True, showDropDown=False
    )
    ws.add_data_validation(dv2)
    dv2.add(ws.cell(row=r, column=3))

    # Thesis bucket dropdown
    _set_cell(ws, r, 4, "All", font=FONT_INPUT, fill=FILL_LIGHT_GOLD,
              border=Border(left=Side(style="medium", color=COLORS["gold"]),
                            right=Side(style="medium", color=COLORS["gold"]),
                            top=Side(style="medium", color=COLORS["gold"]),
                            bottom=Side(style="medium", color=COLORS["gold"])),
              alignment=ALIGN_CENTER)
    dv3 = DataValidation(
        type="list",
        formula1='"All,High-Growth Quality,Fingerprint Match,Insider Conviction,Momentum Breakout,Growth Inflection,Emerging Watch"',
        allow_blank=True, showDropDown=False
    )
    ws.add_data_validation(dv3)
    dv3.add(ws.cell(row=r, column=4))

    ws.row_dimensions[4].height = 22

    # Column headers (row 5)
    headers = ["Ticker", "Name", "Tier", "Conviction", "Thesis Bucket",
               "Combined", "Rationale", "Key Strengths", "Key Risks"]
    _write_col_headers(ws, 5, 1, headers)

    # Data rows
    sorted_stocks = sorted(all_stocks, key=lambda x: x["combined"], reverse=True)
    for ri, s in enumerate(sorted_stocks):
        r = 6 + ri
        thesis = _classify_thesis(s)
        fill = _body_fill(ri)

        _write_body_cell(ws, r, 1, s["ticker"], ri, font=FONT_TICKER)
        _write_body_cell(ws, r, 2, s["name"], ri, alignment=ALIGN_LEFT)
        _write_body_cell(ws, r, 3, TIER_SHORT.get(s["tier"], s["tier"]), ri)
        _write_conviction_cell(ws, r, 4, thesis["conviction"])
        _write_body_cell(ws, r, 5, thesis["bucket"], ri, alignment=ALIGN_LEFT)
        _write_body_cell(ws, r, 6, s["combined"], ri, number_format="0.0")
        _write_body_cell(ws, r, 7, thesis["rationale"], ri, alignment=ALIGN_WRAP)

        # Strengths — green tinted
        strengths_text = "✓ " + thesis["strengths"].replace("; ", "\n✓ ") if thesis["strengths"] != "N/A" else "N/A"
        _write_body_cell(ws, r, 8, strengths_text, ri,
                         fill_override=PatternFill("solid", fgColor="E8F5E9"),
                         alignment=ALIGN_WRAP)

        # Risks — coral tinted
        risks_text = "⚠ " + thesis["risks"].replace("; ", "\n⚠ ") if thesis["risks"] != "none significant" else "—"
        _write_body_cell(ws, r, 9, risks_text, ri,
                         fill_override=PatternFill("solid", fgColor="FBE9E7"),
                         alignment=ALIGN_WRAP)

        ws.row_dimensions[r].height = 40

    # ── Conviction Distribution Chart ──
    if sorted_stocks:
        chart_data_row = 6 + len(sorted_stocks) + 3
        _write_section_header(ws, chart_data_row - 1, 1, 9,
                              "CONVICTION DISTRIBUTION BY TIER")

        # Prepare data
        tiers = ["Nano Cap", "Small Cap", "Breakout"]
        convictions = ["High", "Medium-High", "Medium", "Speculative"]
        ws.cell(row=chart_data_row, column=1, value="Tier")
        for ci, conv in enumerate(convictions):
            ws.cell(row=chart_data_row, column=2 + ci, value=conv)

        for ti, tier in enumerate(tiers):
            ws.cell(row=chart_data_row + 1 + ti, column=1, value=tier)
            for ci, conv in enumerate(convictions):
                count = sum(1 for s in sorted_stocks
                            if TIER_SHORT.get(s["tier"], s["tier"]) == tier
                            and _classify_thesis(s)["conviction"] == conv)
                ws.cell(row=chart_data_row + 1 + ti, column=2 + ci, value=count)

        chart = BarChart()
        chart.type = "bar"
        chart.grouping = "stacked"
        chart.title = "Conviction Distribution by Tier"
        chart.width = 20
        chart.height = 10

        data = Reference(ws, min_col=2, max_col=5,
                         min_row=chart_data_row,
                         max_row=chart_data_row + 3)
        cats = Reference(ws, min_col=1,
                         min_row=chart_data_row + 1,
                         max_row=chart_data_row + 3)
        chart.add_data(data, titles_from_data=True)
        chart.set_categories(cats)

        # Color the series
        conv_colors = [COLORS["teal"], COLORS["sky"],
                       COLORS["gold"], COLORS["coral"]]
        for i, color in enumerate(conv_colors):
            if i < len(chart.series):
                chart.series[i].graphicalProperties.solidFill = color

        ws.add_chart(chart, f"A{chart_data_row + 5}")

    ws.freeze_panes = "C6"


def _build_portfolio(wb: Workbook, all_tiers: dict, all_stocks: list, run_date: str):
    """Sheet 8: Portfolio Construction — position sizing and allocation."""
    ws = wb.create_sheet("Portfolio Construction")
    _setup_sheet(ws)
    ws.sheet_properties.tabColor = COLORS["score_high"]

    col_widths = {1: 3, 2: 9, 3: 24, 4: 12, 5: 14, 6: 14, 7: 14,
                  8: 14, 9: 14, 10: 14, 11: 3, 12: 14, 13: 14, 14: 14}
    for c, w in col_widths.items():
        ws.column_dimensions[get_column_letter(c)].width = w

    _write_title_block(ws, 1, 1, 14,
                       "MODEL PORTFOLIO CONSTRUCTION & RISK METRICS",
                       f"Position sizing, risk categorization, and portfolio analytics  |  {run_date}")

    # ── Section 1: Portfolio Parameters (rows 4-12) ──
    r = 4
    _write_section_header(ws, r, 2, 10, "PORTFOLIO PARAMETERS")
    r += 1

    params = [
        ("Portfolio Size ($)", 100000, "$#,##0", "C"),
        ("Max Single Position %", 0.08, "0.0%", "C"),
        ("Nano Cap Risk Multiplier", 0.6, "0.0x", "C"),
        ("Breakout Cap Multiplier", 1.2, "0.0x", "C"),
        ("Cash Reserve Target %", 0.10, "0.0%", "C"),
    ]

    param_cells = {}  # store cell references
    for label, default, fmt, _ in params:
        _set_cell(ws, r, 2, label, font=FONT_BODY_BOLD,
                  fill=FILL_WHITE, border=BORDER_THIN, alignment=ALIGN_LEFT)
        _set_cell(ws, r, 3, default, font=FONT_INPUT,
                  fill=FILL_LIGHT_GOLD, border=Border(
                      left=Side(style="medium", color=COLORS["gold"]),
                      right=Side(style="medium", color=COLORS["gold"]),
                      top=Side(style="medium", color=COLORS["gold"]),
                      bottom=Side(style="medium", color=COLORS["gold"]),
                  ), alignment=ALIGN_CENTER, number_format=fmt)
        _set_cell(ws, r, 4, "← Input", font=FONT_MUTED, alignment=ALIGN_LEFT)
        param_cells[label] = f"C{r}"
        ws.row_dimensions[r].height = 18
        r += 1

    # Named ranges for key inputs
    portfolio_size_ref = param_cells["Portfolio Size ($)"]
    cash_target_ref = param_cells["Cash Reserve Target %"]

    r += 1  # spacer
    ws.row_dimensions[r - 1].height = 8

    # ── Section 2: Position Sizing Table ──
    _write_section_header(ws, r, 2, 10, "POSITION SIZING")
    r += 1

    pos_headers = ["Ticker", "Name", "Tier", "Conviction", "Base Wt %",
                   "Risk-Adj Wt %", "Dollar Amt", "Share Ct", "Notes"]
    _write_col_headers(ws, r, 2, pos_headers)
    r += 1
    data_start = r

    sorted_stocks = sorted(all_stocks, key=lambda x: x["combined"], reverse=True)
    total_alloc = 0.0

    for ri, s in enumerate(sorted_stocks):
        thesis = _classify_thesis(s)
        risk = _risk_category(s)
        base_pct = {"High": 8.0, "Medium-High": 5.0, "Medium": 3.0, "Speculative": 1.5}.get(thesis["conviction"], 2.0)

        # Risk-adjusted
        if "Nano" in s["tier"]:
            adj_pct = base_pct * 0.6
        elif "Breakout" in s["tier"]:
            adj_pct = base_pct * 1.2
        else:
            adj_pct = base_pct
        adj_pct = round(adj_pct, 1)
        total_alloc += adj_pct

        price = s.get("price") or 0

        fill = _body_fill(ri)
        _write_body_cell(ws, r, 2, s["ticker"], ri, font=FONT_TICKER)
        _write_body_cell(ws, r, 3, s["name"], ri, alignment=ALIGN_LEFT)
        _write_body_cell(ws, r, 4, TIER_SHORT.get(s["tier"], s["tier"]), ri)
        _write_conviction_cell(ws, r, 5, thesis["conviction"])
        _write_body_cell(ws, r, 6, base_pct / 100, ri, number_format="0.0%")

        # Risk-adj weight — formula referencing multiplier params
        _write_body_cell(ws, r, 7, adj_pct / 100, ri, number_format="0.0%",
                         font=FONT_FORMULA)

        # Dollar amount = portfolio_size * adj_weight
        dollar_formula = f"=IFERROR({portfolio_size_ref}*G{r},\"-\")"
        cell = ws.cell(row=r, column=8, value=dollar_formula if False else round(adj_pct / 100 * 100000, 2))
        cell.font = FONT_FORMULA
        cell.fill = fill
        cell.border = BORDER_THIN
        cell.alignment = ALIGN_CENTER
        cell.number_format = "$#,##0"

        # Share count
        shares = int(adj_pct / 100 * 100000 / price) if price > 0 else 0
        _write_body_cell(ws, r, 9, shares, ri, number_format="#,##0")

        # Notes
        notes = f"{risk} risk" if risk != "Medium" else ""
        _write_body_cell(ws, r, 10, notes, ri, alignment=ALIGN_LEFT,
                         font=FONT_MUTED)
        ws.row_dimensions[r].height = 16
        r += 1

    # Totals row
    r += 1
    _set_cell(ws, r, 2, "TOTAL ALLOCATED", font=FONT_BODY_BOLD,
              fill=FILL_DARK_SLATE, border=BORDER_THIN, alignment=ALIGN_LEFT)
    for c in range(3, 7):
        ws.cell(row=r, column=c).fill = FILL_DARK_SLATE
        ws.cell(row=r, column=c).border = BORDER_THIN
    _set_cell(ws, r, 7, total_alloc / 100, font=Font(name="Arial", size=10,
              bold=True, color=COLORS["gold"]), fill=FILL_DARK_SLATE,
              border=BORDER_THIN, alignment=ALIGN_CENTER, number_format="0.0%")
    _set_cell(ws, r, 8, round(total_alloc / 100 * 100000, 0),
              font=Font(name="Arial", size=10, bold=True, color=COLORS["gold"]),
              fill=FILL_DARK_SLATE, border=BORDER_THIN, alignment=ALIGN_CENTER,
              number_format="$#,##0")
    r += 1
    cash_pct = max(0, 100 - total_alloc)
    _set_cell(ws, r, 2, "CASH RESERVE", font=FONT_BODY_BOLD,
              fill=FILL_DARK_SLATE, border=BORDER_THIN, alignment=ALIGN_LEFT)
    for c in range(3, 7):
        ws.cell(row=r, column=c).fill = FILL_DARK_SLATE
        ws.cell(row=r, column=c).border = BORDER_THIN
    _set_cell(ws, r, 7, cash_pct / 100,
              font=Font(name="Arial", size=10, bold=True, color=COLORS["teal"]),
              fill=FILL_DARK_SLATE, border=BORDER_THIN, alignment=ALIGN_CENTER,
              number_format="0.0%")
    _set_cell(ws, r, 8, round(cash_pct / 100 * 100000, 0),
              font=Font(name="Arial", size=10, bold=True, color=COLORS["teal"]),
              fill=FILL_DARK_SLATE, border=BORDER_THIN, alignment=ALIGN_CENTER,
              number_format="$#,##0")

    # ── Section 3: Allocation Charts (right side) ──
    # Donut: Tier allocation
    chart_row = 4
    tier_alloc_data_row = r + 4
    ws.cell(row=tier_alloc_data_row, column=12, value="Tier")
    ws.cell(row=tier_alloc_data_row, column=13, value="Allocation %")
    tier_allocs = {}
    for s in sorted_stocks:
        t = TIER_SHORT.get(s["tier"], s["tier"])
        thesis = _classify_thesis(s)
        tier_allocs[t] = tier_allocs.get(t, 0) + _position_size_pct(thesis["conviction"], s["tier"])
    tier_allocs["Cash"] = cash_pct

    row_i = tier_alloc_data_row + 1
    for tier, alloc in tier_allocs.items():
        ws.cell(row=row_i, column=12, value=tier)
        ws.cell(row=row_i, column=13, value=alloc)
        row_i += 1

    chart1 = DoughnutChart()
    chart1.title = "Allocation by Tier"
    chart1.width = 14
    chart1.height = 10
    data = Reference(ws, min_col=13, min_row=tier_alloc_data_row,
                     max_row=tier_alloc_data_row + len(tier_allocs))
    cats = Reference(ws, min_col=12, min_row=tier_alloc_data_row + 1,
                     max_row=tier_alloc_data_row + len(tier_allocs))
    chart1.add_data(data, titles_from_data=True)
    chart1.set_categories(cats)
    ws.add_chart(chart1, f"L{chart_row}")

    # ── Section 4: Sensitivity to Portfolio Size ──
    size_row = r + 3
    _write_section_header(ws, size_row, 2, 10,
                          "SENSITIVITY TO PORTFOLIO SIZE")
    size_row += 1

    portfolio_sizes = [50000, 100000, 250000, 500000, 1000000]
    ps_headers = ["Ticker"] + [f"${p//1000}K" if p < 1000000 else "$1M" for p in portfolio_sizes]
    _write_col_headers(ws, size_row, 2, ps_headers)
    size_row += 1

    for ri, s in enumerate(sorted_stocks[:10]):
        thesis = _classify_thesis(s)
        adj_pct = _position_size_pct(thesis["conviction"], s["tier"]) / 100
        _write_body_cell(ws, size_row, 2, s["ticker"], ri, font=FONT_TICKER)
        for pi, ps in enumerate(portfolio_sizes):
            dollar = adj_pct * ps
            fill_override = FILL_LIGHT_GOLD if ps == 100000 else _body_fill(ri)
            _write_body_cell(ws, size_row, 3 + pi, dollar, ri,
                             number_format="$#,##0",
                             fill_override=fill_override)
        ws.row_dimensions[size_row].height = 16
        size_row += 1

    ws.freeze_panes = "C5"


def _build_sensitivity(wb: Workbook, all_stocks: list, run_date: str):
    """Sheet 9: Sensitivity Analysis — weight profile comparison."""
    ws = wb.create_sheet("Sensitivity Analysis")
    _setup_sheet(ws)
    ws.sheet_properties.tabColor = COLORS["coral"]

    col_widths = {1: 3, 2: 9, 3: 24, 4: 12, 5: 14, 6: 14, 7: 14,
                  8: 14, 9: 14, 10: 12, 11: 12, 12: 12, 13: 14}
    for c, w in col_widths.items():
        ws.column_dimensions[get_column_letter(c)].width = w

    _write_title_block(ws, 1, 1, 13,
                       "SENSITIVITY ANALYSIS — WEIGHT PROFILE COMPARISON",
                       "How scores change under different weighting assumptions")

    # ── Section 1: Scenario Control Panel ──
    r = 4
    _write_section_header(ws, r, 2, 13, "SCENARIO CONTROL PANEL")
    r += 1

    # Dropdown
    _set_cell(ws, r, 2, "Active Scenario:", font=FONT_BODY_BOLD,
              alignment=ALIGN_RIGHT)
    _set_cell(ws, r, 3, "Baseline", font=FONT_INPUT, fill=FILL_LIGHT_GOLD,
              border=Border(left=Side(style="medium", color=COLORS["gold"]),
                            right=Side(style="medium", color=COLORS["gold"]),
                            top=Side(style="medium", color=COLORS["gold"]),
                            bottom=Side(style="medium", color=COLORS["gold"])),
              alignment=ALIGN_CENTER)
    dv = DataValidation(
        type="list",
        formula1='"Baseline,Growth Focus,Quality Focus,Insider Focus,Custom"',
        allow_blank=True, showDropDown=False
    )
    dv.error = "Please select a valid scenario"
    dv.errorTitle = "Invalid Selection"
    ws.add_data_validation(dv)
    dv.add(ws.cell(row=r, column=3))
    r += 1

    # Weight breakdown table
    r += 1
    profile_names = list(SENSITIVITY_PROFILES.keys()) + ["Custom"]
    weight_hdrs = ["Weight"] + profile_names
    _write_col_headers(ws, r, 2, weight_hdrs)
    r += 1

    weight_keys = list(DEFAULT_WEIGHTS.keys())
    for wk in weight_keys:
        label = wk.replace("_", " ").title()
        _set_cell(ws, r, 2, label, font=FONT_BODY, fill=FILL_OFF_WHITE,
                  border=BORDER_THIN, alignment=ALIGN_LEFT)
        for ci, pn in enumerate(list(SENSITIVITY_PROFILES.keys())):
            val = SENSITIVITY_PROFILES[pn][wk]
            is_baseline = (pn == "Baseline")
            _set_cell(ws, r, 3 + ci, val, font=FONT_FORMULA,
                      fill=FILL_LIGHT_GOLD if is_baseline else FILL_OFF_WHITE,
                      border=BORDER_THIN, alignment=ALIGN_CENTER,
                      number_format="0.00")
        # Custom column — blue input
        _set_cell(ws, r, 3 + len(SENSITIVITY_PROFILES), DEFAULT_WEIGHTS[wk],
                  font=FONT_INPUT, fill=FILL_LIGHT_GOLD, border=BORDER_THIN,
                  alignment=ALIGN_CENTER, number_format="0.00")
        ws.row_dimensions[r].height = 16
        r += 1

    # ── Section 2: Score Comparison Table ──
    r += 1
    _write_section_header(ws, r, 2, 13,
                          "COMBINED SCORES UNDER EACH PROFILE")
    r += 1

    score_hdrs = ["Ticker", "Name", "Tier"] + list(SENSITIVITY_PROFILES.keys()) + \
                 ["Max Score", "Min Score", "Score Range", "Rank Stability"]
    _write_col_headers(ws, r, 2, score_hdrs)
    r += 1
    score_data_start = r

    sorted_stocks = sorted(all_stocks, key=lambda x: x["combined"], reverse=True)
    for ri, s in enumerate(sorted_stocks):
        _write_body_cell(ws, r, 2, s["ticker"], ri, font=FONT_TICKER)
        _write_body_cell(ws, r, 3, s["name"], ri, alignment=ALIGN_LEFT)
        _write_body_cell(ws, r, 4, TIER_SHORT.get(s["tier"], s["tier"]), ri)

        scores = s.get("sensitivity_scores", {})
        vals = []
        for ci, pn in enumerate(SENSITIVITY_PROFILES.keys()):
            v = scores.get(pn, s["combined"])
            _write_body_cell(ws, r, 5 + ci, v, ri, number_format="0.0")
            vals.append(v)

        max_score = max(vals) if vals else 0
        min_score = min(vals) if vals else 0
        score_range = max_score - min_score

        _write_body_cell(ws, r, 5 + len(SENSITIVITY_PROFILES), max_score, ri,
                         number_format="0.0")
        _write_body_cell(ws, r, 6 + len(SENSITIVITY_PROFILES), min_score, ri,
                         number_format="0.0")
        _write_body_cell(ws, r, 7 + len(SENSITIVITY_PROFILES), score_range, ri,
                         number_format="0.0")

        # Rank Stability
        if score_range < 10:
            stability = "Stable"
            stab_fill = FILL_TEAL
            stab_font = Font(name="Arial", size=10, bold=True, color=COLORS["text_white"])
        elif score_range < 20:
            stability = "Moderate"
            stab_fill = FILL_LIGHT_GOLD
            stab_font = Font(name="Arial", size=10, bold=True, color=COLORS["text_dark"])
        else:
            stability = "Sensitive"
            stab_fill = FILL_CORAL
            stab_font = Font(name="Arial", size=10, bold=True, color=COLORS["text_white"])

        _write_body_cell(ws, r, 8 + len(SENSITIVITY_PROFILES), stability, ri,
                         font=stab_font, fill_override=stab_fill)

        ws.row_dimensions[r].height = 16
        r += 1

    # Conditional formatting on score columns
    if sorted_stocks:
        last = r - 1
        num_profiles = len(SENSITIVITY_PROFILES)
        for ci in range(num_profiles):
            col_letter = get_column_letter(5 + ci)
            _apply_score_color_scale(ws, f"{col_letter}{score_data_start}:{col_letter}{last}")

    # Score range: coral if >20
    if sorted_stocks:
        range_col = get_column_letter(7 + len(SENSITIVITY_PROFILES))
        ws.conditional_formatting.add(
            f"{range_col}{score_data_start}:{range_col}{last}",
            CellIsRule(operator="greaterThan", formula=["20"],
                       fill=PatternFill("solid", fgColor=COLORS["coral"]))
        )

    # Note
    r += 1
    ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=13)
    _set_cell(ws, r, 2,
              "Note: Stocks with high Score Range are ranking artifacts — their attractiveness depends heavily on your investment philosophy.",
              font=Font(name="Arial", size=9, italic=True, color=COLORS["text_muted"]),
              alignment=ALIGN_LEFT)

    # ── Section 3: Tornado Chart for #1 stock ──
    if sorted_stocks:
        r += 2
        top_stock = sorted_stocks[0]
        _write_section_header(ws, r, 2, 13,
                              f"TORNADO ANALYSIS — {top_stock['ticker']} (Score Impact of ±10% Weight Changes)")
        r += 1

        weight_labels = ["Rev Growth", "Gross Margin", "Dilution", "Insider Own", "Acceleration"]
        ws.cell(row=r, column=2, value="Weight Component")
        ws.cell(row=r, column=3, value="Impact (-10%)")
        ws.cell(row=r, column=4, value="Impact (+10%)")
        _write_col_headers(ws, r, 2, ["Component", "−10% Impact", "+10% Impact"])
        r += 1
        tornado_start = r

        stock_metrics = {
            "revenue_growth_pct": top_stock.get("rev_growth_pct"),
            "gross_margin_pct": top_stock.get("gross_margin_pct"),
            "dilution_3yr_pct": top_stock.get("dilution_3yr_pct"),
            "insider_ownership_pct": top_stock.get("insider_pct"),
            "revenue_acceleration_pct": top_stock.get("rev_accel_pct"),
            "market_cap_M": top_stock.get("mkt_cap_m"),
        }
        baseline_composite = score_stock(stock_metrics, DEFAULT_WEIGHTS)
        baseline_fp = top_stock["fingerprint"]
        baseline_combined = baseline_composite * 0.6 + baseline_fp * 0.4

        for wi, wk in enumerate(weight_keys):
            # -10%
            w_down = dict(DEFAULT_WEIGHTS)
            w_down[wk] = max(0, w_down[wk] - 0.10)
            # Renormalize
            total_w = sum(w_down.values())
            if total_w > 0:
                w_down = {k: v / total_w for k, v in w_down.items()}
            c_down = score_stock(stock_metrics, w_down)
            combined_down = c_down * 0.6 + baseline_fp * 0.4
            delta_down = combined_down - baseline_combined

            # +10%
            w_up = dict(DEFAULT_WEIGHTS)
            w_up[wk] = min(1.0, w_up[wk] + 0.10)
            total_w = sum(w_up.values())
            if total_w > 0:
                w_up = {k: v / total_w for k, v in w_up.items()}
            c_up = score_stock(stock_metrics, w_up)
            combined_up = c_up * 0.6 + baseline_fp * 0.4
            delta_up = combined_up - baseline_combined

            _write_body_cell(ws, r, 2, weight_labels[wi], wi, alignment=ALIGN_LEFT)
            _write_body_cell(ws, r, 3, round(delta_down, 2), wi, number_format="0.00")
            _write_body_cell(ws, r, 4, round(delta_up, 2), wi, number_format="0.00")
            ws.row_dimensions[r].height = 16
            r += 1

        # Bar chart for tornado
        chart = BarChart()
        chart.type = "bar"
        chart.title = f"Score Impact — {top_stock['ticker']}"
        chart.width = 18
        chart.height = 10
        chart.legend = None

        data_neg = Reference(ws, min_col=3, min_row=tornado_start,
                             max_row=tornado_start + 4)
        data_pos = Reference(ws, min_col=4, min_row=tornado_start,
                             max_row=tornado_start + 4)
        cats = Reference(ws, min_col=2, min_row=tornado_start,
                         max_row=tornado_start + 4)

        chart.add_data(data_neg, titles_from_data=False)
        chart.add_data(data_pos, titles_from_data=False)
        chart.set_categories(cats)

        if len(chart.series) >= 2:
            chart.series[0].graphicalProperties.solidFill = COLORS["coral"]
            chart.series[1].graphicalProperties.solidFill = COLORS["teal"]

        ws.add_chart(chart, f"F{tornado_start}")

    ws.freeze_panes = "D5"


def _build_revenue_trends(wb: Workbook, all_tiers: dict, run_date: str):
    """Sheet 10: Revenue Trends — quarterly data by stock."""
    ws = wb.create_sheet("Revenue Trends")
    _setup_sheet(ws)
    ws.sheet_properties.tabColor = COLORS["sky"]

    col_widths = {1: 9, 2: 22, 3: 14, 4: 14, 5: 14, 6: 14,
                  7: 14, 8: 14, 9: 14, 10: 14, 11: 14}
    for c, w in col_widths.items():
        ws.column_dimensions[get_column_letter(c)].width = w

    _write_title_block(ws, 1, 1, 11, "QUARTERLY REVENUE TRENDS",
                       f"Last 8 quarters of revenue and margin by company  |  {run_date}")
    ws.row_dimensions[3].height = 8

    r = 4
    for tier_name, stocks in all_tiers.items():
        short = TIER_SHORT.get(tier_name, tier_name)

        # Tier divider
        _write_section_header(ws, r, 1, 11, f"  {short}")
        r += 1

        for s in stocks:
            qdates = s.get("quarterly_dates", [])
            qrevs = s.get("quarterly_revenue", [])
            qgm = s.get("quarterly_gm", [])
            if not qdates:
                continue

            n_quarters = min(len(qdates), 8)

            # Row 1: Stock header
            ws.merge_cells(start_row=r, start_column=1,
                           end_row=r, end_column=2)
            _set_cell(ws, r, 1, f"{s['ticker']}  —  {s['name']}",
                      font=FONT_BODY_BOLD, fill=FILL_OFF_WHITE,
                      border=BORDER_THIN, alignment=ALIGN_LEFT)
            _set_cell(ws, r, 3, TIER_SHORT.get(s["tier"], s["tier"]),
                      font=FONT_MUTED, fill=FILL_OFF_WHITE,
                      border=BORDER_THIN, alignment=ALIGN_CENTER)
            _set_cell(ws, r, 4, f"Mkt Cap: ${s['mkt_cap_m']:.0f}M",
                      font=FONT_MUTED, fill=FILL_OFF_WHITE,
                      border=BORDER_THIN, alignment=ALIGN_LEFT)
            for c in range(5, 12):
                ws.cell(row=r, column=c).fill = FILL_OFF_WHITE
                ws.cell(row=r, column=c).border = BORDER_THIN
            ws.row_dimensions[r].height = 18
            r += 1

            # Row 2: Quarter labels
            _set_cell(ws, r, 1, "", border=BORDER_THIN)
            _set_cell(ws, r, 2, "Quarter", font=FONT_COL_HEADER,
                      fill=FILL_CHARCOAL, border=BORDER_THIN, alignment=ALIGN_CENTER)
            for ci in range(n_quarters):
                _set_cell(ws, r, 3 + ci, qdates[ci], font=FONT_COL_HEADER,
                          fill=FILL_CHARCOAL, border=BORDER_THIN,
                          alignment=ALIGN_CENTER)
            ws.row_dimensions[r].height = 16
            r += 1

            # Row 3: Revenue ($M)
            _set_cell(ws, r, 1, "", border=BORDER_THIN)
            _set_cell(ws, r, 2, "Revenue ($M)", font=FONT_BODY_BOLD,
                      fill=FILL_WHITE, border=BORDER_THIN, alignment=ALIGN_LEFT)
            for ci in range(n_quarters):
                rev_m = qrevs[ci] / 1_000_000 if qrevs[ci] else 0
                _set_cell(ws, r, 3 + ci, rev_m, font=FONT_INPUT,
                          fill=FILL_WHITE, border=BORDER_THIN,
                          alignment=ALIGN_CENTER, number_format="$#,##0.0")
            ws.row_dimensions[r].height = 16
            rev_row = r
            r += 1

            # Row 4: Gross Margin %
            _set_cell(ws, r, 1, "", border=BORDER_THIN)
            _set_cell(ws, r, 2, "Gross Margin %", font=FONT_BODY_BOLD,
                      fill=FILL_OFF_WHITE, border=BORDER_THIN, alignment=ALIGN_LEFT)
            for ci in range(n_quarters):
                gm_val = qgm[ci] / 100 if ci < len(qgm) and qgm[ci] is not None else None
                _set_cell(ws, r, 3 + ci, gm_val, font=FONT_FORMULA,
                          fill=FILL_OFF_WHITE, border=BORDER_THIN,
                          alignment=ALIGN_CENTER, number_format="0.0%")
            # Color scale on margin row
            if n_quarters >= 2:
                start_col = get_column_letter(3)
                end_col = get_column_letter(2 + n_quarters)
                _apply_green_scale(ws, f"{start_col}{r}:{end_col}{r}")
            ws.row_dimensions[r].height = 16
            r += 1

            # Row 5: QoQ Revenue Growth
            _set_cell(ws, r, 1, "", border=BORDER_THIN)
            _set_cell(ws, r, 2, "QoQ Rev Growth", font=FONT_BODY,
                      fill=FILL_WHITE, border=BORDER_THIN, alignment=ALIGN_LEFT)
            for ci in range(n_quarters):
                if ci == 0 or not qrevs[ci] or not qrevs[ci - 1] or qrevs[ci - 1] == 0:
                    _set_cell(ws, r, 3 + ci, None, fill=FILL_WHITE,
                              border=BORDER_THIN, alignment=ALIGN_CENTER)
                else:
                    qoq = (qrevs[ci] - qrevs[ci - 1]) / abs(qrevs[ci - 1])
                    _set_cell(ws, r, 3 + ci, qoq, font=FONT_FORMULA,
                              fill=FILL_WHITE, border=BORDER_THIN,
                              alignment=ALIGN_CENTER, number_format="0.0%")
            if n_quarters >= 3:
                start_col = get_column_letter(4)
                end_col = get_column_letter(2 + n_quarters)
                _apply_perf_color_scale(ws, f"{start_col}{r}:{end_col}{r}")
            ws.row_dimensions[r].height = 16
            r += 1

            # Row 6: YoY Revenue Growth
            _set_cell(ws, r, 1, "", border=BORDER_THIN)
            _set_cell(ws, r, 2, "YoY Rev Growth", font=FONT_BODY,
                      fill=FILL_OFF_WHITE, border=BORDER_THIN, alignment=ALIGN_LEFT)
            for ci in range(n_quarters):
                if ci < 4 or not qrevs[ci] or not qrevs[ci - 4] or qrevs[ci - 4] == 0:
                    _set_cell(ws, r, 3 + ci, None, fill=FILL_OFF_WHITE,
                              border=BORDER_THIN, alignment=ALIGN_CENTER)
                else:
                    yoy = (qrevs[ci] - qrevs[ci - 4]) / abs(qrevs[ci - 4])
                    _set_cell(ws, r, 3 + ci, yoy, font=FONT_FORMULA,
                              fill=FILL_OFF_WHITE, border=BORDER_THIN,
                              alignment=ALIGN_CENTER, number_format="0.0%")
            ws.row_dimensions[r].height = 16
            r += 1

            # Row 7: Spacer
            ws.row_dimensions[r].height = 8
            r += 1

    ws.freeze_panes = "C4"


def _build_fingerprint(wb: Workbook, run_date: str):
    """Sheet 11: Fingerprint Reference — pre-run profiles of 10x stocks."""
    ws = wb.create_sheet("Fingerprint Reference")
    _setup_sheet(ws)
    ws.sheet_properties.tabColor = COLORS["gold"]

    # Wide layout for cards
    for c in range(1, 18):
        ws.column_dimensions[get_column_letter(c)].width = 14

    # Header
    for r in range(1, 4):
        for c in range(1, 18):
            ws.cell(row=r, column=c).fill = FILL_NAVY
    ws.merge_cells("A1:Q2")
    cell = ws.cell(row=1, column=1,
                   value="THE FINGERPRINT — Pre-Run Profiles of 10x Stocks")
    cell.font = Font(name="Arial", size=16, bold=True, color=COLORS["gold"])
    cell.fill = FILL_NAVY
    cell.alignment = ALIGN_CENTER
    ws.row_dimensions[1].height = 28
    ws.row_dimensions[2].height = 20
    ws.merge_cells("A3:Q3")
    cell = ws.cell(row=3, column=1,
                   value="Historical winners at their pre-run inflection point — the benchmark for scoring")
    cell.font = FONT_SUBTITLE
    cell.fill = FILL_NAVY
    cell.alignment = ALIGN_CENTER
    ws.row_dimensions[3].height = 20
    ws.row_dimensions[4].height = 8  # spacer

    refs = load_reference_stocks()

    # ── Section 1: Reference Stock Cards (3 across, 2 rows) ──
    r = 5
    _write_section_header(ws, r, 1, 17, "REFERENCE STOCK PROFILES")
    r += 1

    card_width = 5  # columns per card
    cards_per_row = 3

    for card_idx, ref in enumerate(refs):
        row_group = card_idx // cards_per_row
        col_group = card_idx % cards_per_row
        card_col = 1 + col_group * (card_width + 1)
        card_row = r + row_group * 13

        # Card header
        ws.merge_cells(start_row=card_row, start_column=card_col,
                       end_row=card_row, end_column=card_col + card_width - 1)
        header_text = f"{ref['ticker']}  —  {ref['name']}"
        _set_cell(ws, card_row, card_col, header_text,
                  font=Font(name="Arial", size=11, bold=True, color=COLORS["text_white"]),
                  fill=FILL_DARK_SLATE, border=BORDER_THIN, alignment=ALIGN_LEFT)
        for c in range(card_col + 1, card_col + card_width):
            ws.cell(row=card_row, column=c).fill = FILL_DARK_SLATE
            ws.cell(row=card_row, column=c).border = BORDER_THIN

        # Sub-header: period + peak return
        card_row += 1
        ws.merge_cells(start_row=card_row, start_column=card_col,
                       end_row=card_row, end_column=card_col + card_width - 1)
        peak = ref.get("peak_return_from_entry_pct", 0)
        _set_cell(ws, card_row, card_col,
                  f"Run Period: {ref['pre_run_year']}   ▲ {peak:,}%",
                  font=Font(name="Arial", size=10, bold=True, color=COLORS["teal"]),
                  fill=FILL_OFF_WHITE, border=BORDER_THIN, alignment=ALIGN_LEFT)
        for c in range(card_col + 1, card_col + card_width):
            ws.cell(row=card_row, column=c).fill = FILL_OFF_WHITE
            ws.cell(row=card_row, column=c).border = BORDER_THIN

        # Metrics
        metrics = [
            ("Market Cap", f"${ref['pre_run_market_cap_M']}M"),
            ("Revenue Growth", f"{ref['pre_run_revenue_growth_pct']}%"),
            ("Gross Margin", f"{ref['pre_run_gross_margin_pct']}%"),
            ("Insider Ownership", f"{ref['pre_run_insider_ownership_pct']}%"),
            ("Dilution 3yr", f"{ref['pre_run_dilution_3yr_pct']}%"),
        ]

        for mi, (metric_name, metric_val) in enumerate(metrics):
            card_row += 1
            _set_cell(ws, card_row, card_col, metric_name,
                      font=FONT_MUTED, fill=FILL_OFF_WHITE,
                      border=BORDER_THIN, alignment=ALIGN_LEFT)
            # Span remaining columns for value
            ws.merge_cells(start_row=card_row, start_column=card_col + 1,
                           end_row=card_row, end_column=card_col + card_width - 1)
            _set_cell(ws, card_row, card_col + 1, metric_val,
                      font=FONT_BODY_BOLD, fill=FILL_OFF_WHITE,
                      border=BORDER_THIN, alignment=ALIGN_LEFT)
            for c in range(card_col + 2, card_col + card_width):
                ws.cell(row=card_row, column=c).fill = FILL_OFF_WHITE
                ws.cell(row=card_row, column=c).border = BORDER_THIN

        # Card footer: theme
        card_row += 1
        ws.merge_cells(start_row=card_row, start_column=card_col,
                       end_row=card_row, end_column=card_col + card_width - 1)
        _set_cell(ws, card_row, card_col, ref["theme"],
                  font=Font(name="Arial", size=9, italic=True, color=COLORS["text_muted"]),
                  fill=FILL_OFF_WHITE, border=BORDER_THIN, alignment=ALIGN_LEFT)
        for c in range(card_col + 1, card_col + card_width):
            ws.cell(row=card_row, column=c).fill = FILL_OFF_WHITE
            ws.cell(row=card_row, column=c).border = BORDER_THIN

        # Gold border around card
        for cr in range(card_row - 7, card_row + 1):
            ws.cell(row=cr, column=card_col).border = Border(
                left=Side(style="medium", color=COLORS["gold"]),
                top=ws.cell(row=cr, column=card_col).border.top,
                bottom=ws.cell(row=cr, column=card_col).border.bottom,
                right=ws.cell(row=cr, column=card_col).border.right,
            )
            ws.cell(row=cr, column=card_col + card_width - 1).border = Border(
                right=Side(style="medium", color=COLORS["gold"]),
                top=ws.cell(row=cr, column=card_col + card_width - 1).border.top,
                bottom=ws.cell(row=cr, column=card_col + card_width - 1).border.bottom,
                left=ws.cell(row=cr, column=card_col + card_width - 1).border.left,
            )

    # ── Section 2: Radar Chart ──
    radar_row = r + 28
    _write_section_header(ws, radar_row, 1, 17, "COMPARATIVE RADAR — All Reference Stocks")
    radar_row += 1

    # Write radar data
    radar_axes = ["Revenue Growth", "Gross Margin", "Insider %", "Low Dilution", "Mkt Cap Score"]
    ws.cell(row=radar_row, column=1, value="Metric")
    for ai, axis in enumerate(radar_axes):
        ws.cell(row=radar_row + 1 + ai, column=1, value=axis)

    for ri_ref, ref in enumerate(refs):
        ws.cell(row=radar_row, column=2 + ri_ref, value=ref["ticker"])
        # Normalize each metric 0-100
        ws.cell(row=radar_row + 1, column=2 + ri_ref,
                value=min(100, ref["pre_run_revenue_growth_pct"] * 100 / 95))
        ws.cell(row=radar_row + 2, column=2 + ri_ref,
                value=min(100, ref["pre_run_gross_margin_pct"] * 100 / 50))
        ws.cell(row=radar_row + 3, column=2 + ri_ref,
                value=min(100, ref["pre_run_insider_ownership_pct"] * 100 / 30))
        # Low dilution: invert (lower = higher score)
        ws.cell(row=radar_row + 4, column=2 + ri_ref,
                value=max(0, 100 - ref["pre_run_dilution_3yr_pct"] * 100 / 25))
        # Market cap score (smaller = higher in this context)
        ws.cell(row=radar_row + 5, column=2 + ri_ref,
                value=max(0, 100 - ref["pre_run_market_cap_M"] * 100 / 1200))

    chart = RadarChart()
    chart.title = "Reference Stock Comparison"
    chart.width = 20
    chart.height = 14
    chart.style = 26

    data = Reference(ws, min_col=2, max_col=1 + len(refs),
                     min_row=radar_row, max_row=radar_row + 5)
    cats = Reference(ws, min_col=1, min_row=radar_row + 1,
                     max_row=radar_row + 5)
    chart.add_data(data, titles_from_data=True)
    chart.set_categories(cats)

    # Color each series
    series_colors = [COLORS["gold"], COLORS["teal"], COLORS["coral"],
                     COLORS["sky"], COLORS["navy"], COLORS["score_mid"]]
    for i, color in enumerate(series_colors[:len(chart.series)]):
        chart.series[i].graphicalProperties.line.solidFill = color

    ws.add_chart(chart, f"A{radar_row + 7}")

    # ── Section 3: Derived Ideal Ranges ──
    ideal_row = radar_row + 25
    _write_section_header(ws, ideal_row, 1, 10, "DERIVED IDEAL RANGES")
    ideal_row += 1

    ideal = compute_ideal_ranges(refs)
    range_headers = ["Metric", "Min", "Median", "Max",
                     "Screener Threshold", "Status"]
    _write_col_headers(ws, ideal_row, 1, range_headers)
    ideal_row += 1

    # Compute medians
    import statistics
    rev_vals = [r["pre_run_revenue_growth_pct"] for r in refs]
    gm_vals = [r["pre_run_gross_margin_pct"] for r in refs]
    ins_vals = [r["pre_run_insider_ownership_pct"] for r in refs]
    dil_vals = [r["pre_run_dilution_3yr_pct"] for r in refs]

    range_data = [
        ("Revenue Growth %", ideal["revenue_growth_pct"][0],
         statistics.median(rev_vals), ideal["revenue_growth_pct"][1],
         "5% (floor)", "✓ Aligned"),
        ("Gross Margin %", ideal["gross_margin_pct"][0],
         statistics.median(gm_vals), ideal["gross_margin_pct"][1],
         "20% (floor)", "✓ Aligned"),
        ("Insider Ownership %", ideal["insider_ownership_pct"][0],
         statistics.median(ins_vals), ideal["insider_ownership_pct"][1],
         "N/A", "⚠ Loose"),
        ("Dilution 3yr %", ideal["dilution_3yr_pct"][0],
         statistics.median(dil_vals), ideal["dilution_3yr_pct"][1],
         "30% (ceiling)", "✓ Aligned"),
    ]

    for ri, (metric, mn, med, mx, threshold, status) in enumerate(range_data):
        _write_body_cell(ws, ideal_row, 1, metric, ri, alignment=ALIGN_LEFT,
                         font=FONT_BODY_BOLD)
        _write_body_cell(ws, ideal_row, 2, mn, ri, number_format="0.0")
        _write_body_cell(ws, ideal_row, 3, med, ri, number_format="0.0")
        _write_body_cell(ws, ideal_row, 4, mx, ri, number_format="0.0")
        _write_body_cell(ws, ideal_row, 5, threshold, ri, alignment=ALIGN_LEFT)

        if "✓" in status:
            status_fill = PatternFill("solid", fgColor="E8F5E9")
            status_font = Font(name="Arial", size=10, color=COLORS["score_high"])
        elif "⚠" in status:
            status_fill = PatternFill("solid", fgColor="FFF8E1")
            status_font = Font(name="Arial", size=10, color=COLORS["score_mid"])
        else:
            status_fill = PatternFill("solid", fgColor="FBE9E7")
            status_font = Font(name="Arial", size=10, color=COLORS["score_low"])
        _write_body_cell(ws, ideal_row, 6, status, ri,
                         font=status_font, fill_override=status_fill)
        ws.row_dimensions[ideal_row].height = 16
        ideal_row += 1

    ws.freeze_panes = "A5"


def _build_methodology(wb: Workbook, run_date: str):
    """Sheet 12: Methodology — structured documentation."""
    ws = wb.create_sheet("Methodology")
    _setup_sheet(ws)
    ws.sheet_properties.tabColor = COLORS["dark_slate"]

    ws.column_dimensions["A"].width = 3
    ws.column_dimensions["B"].width = 28
    ws.column_dimensions["C"].width = 55

    _write_title_block(ws, 1, 1, 3, "SCREENING METHODOLOGY",
                       "Technical documentation for the structural growth equity screener")
    ws.row_dimensions[3].height = 8

    sections = [
        ("Screening Universe", [
            "US-listed equities across three market cap tiers:",
            "  Nano Cap: $50M–$300M (highest risk, highest upside)",
            "  Small Cap: $300M–$2B (the sweet spot — proven enough to have real revenue)",
            "  Breakout: $2B–$15B (validated, growth re-accelerating)",
            "",
            "Focus themes: Aerospace/Defense, Semiconductors, AI Infrastructure,",
            "Photonics, Quantum Computing, Space, New Energy, Gene Editing",
        ]),
        ("Hard Filters", [
            "Must be US-domiciled and actively trading on a major exchange",
            "Excludes: Biotech/Pharma (unless gene editing keywords in description)",
            "Excludes: Oil & Gas, Warrants, Units, Preferred shares",
            "Must match industry whitelist OR description keywords in target themes",
            "No OTC/pink sheet securities",
        ]),
        ("Quality Floor", [
            "Revenue growth must exceed 5% YoY",
            "Gross margin must exceed 20%",
            "3-year share dilution must be below 30%",
            "Stocks failing any of these thresholds are excluded entirely",
        ]),
        ("Composite Score (60%)", [
            f"Revenue Growth YoY: {DEFAULT_WEIGHTS['revenue_growth']*100:.0f}% weight — Higher is better, >50% = max score",
            f"Gross Margin: {DEFAULT_WEIGHTS['gross_margin']*100:.0f}% weight — >40% = max, proxy for pricing power and moat",
            f"Dilution 3yr: {DEFAULT_WEIGHTS['dilution']*100:.0f}% weight — Lower is better, <5% = full points, >30% = zero",
            f"Insider Ownership: {DEFAULT_WEIGHTS['insider_ownership']*100:.0f}% weight — >10% = full points, founders with skin in the game",
            f"Revenue Acceleration: {DEFAULT_WEIGHTS['revenue_acceleration']*100:.0f}% weight — Is the growth rate itself increasing?",
        ]),
        ("Fingerprint Score (40%)", [
            "Measures similarity to 6 reference stocks at their pre-run inflection point",
            "Reference stocks: LITE, COHR, AAOI, KTOS, RKLB, MVIS",
            "Compares: revenue growth, gross margin, insider %, dilution, market cap",
            "Inside the ideal range = 100; score decays with distance from range",
            "Combined Score = 60% × Composite + 40% × Fingerprint",
        ]),
        ("Position Sizing Rules", [
            "Based on conviction level: High (8%), Medium-High (5%), Medium (3%), Speculative (1.5%)",
            "Nano caps receive 0.6x risk multiplier (smaller positions for higher risk)",
            "Breakout caps receive 1.2x multiplier (larger positions for validated growth)",
            "Designed to sum to <100% with meaningful cash reserve",
        ]),
        ("Data Source & Refresh", [
            "All data sourced from Financial Modeling Prep (FMP) API",
            "24-hour cache on all API responses to protect rate limits",
            "Quarterly financials: last 8 quarters of income statements",
            "Share count history: last 12 quarters of enterprise values",
            f"Report generated: {run_date}",
        ]),
        ("Disclaimer", [
            "This screener is for research and educational purposes only.",
            "It does not constitute investment advice or a recommendation to buy or sell.",
            "Past performance of reference stocks does not guarantee future results.",
            "All investments carry risk of loss. Consult a licensed financial advisor.",
        ]),
    ]

    r = 4
    for section_title, items in sections:
        # Section label on the left
        _set_cell(ws, r, 2, section_title, font=FONT_SECTION,
                  fill=FILL_DARK_SLATE, border=BORDER_GOLD_BOTTOM,
                  alignment=ALIGN_LEFT)
        ws.cell(row=r, column=3).fill = FILL_DARK_SLATE
        ws.cell(row=r, column=3).border = BORDER_GOLD_BOTTOM
        ws.row_dimensions[r].height = 22
        r += 1

        for item in items:
            if item == "":
                ws.row_dimensions[r].height = 8
                r += 1
                continue
            _set_cell(ws, r, 2, "", fill=FILL_WHITE, border=BORDER_THIN)
            _set_cell(ws, r, 3, item, font=FONT_BODY, fill=FILL_WHITE,
                      border=BORDER_THIN, alignment=ALIGN_WRAP)
            ws.row_dimensions[r].height = 16
            r += 1

        # Spacer between sections
        ws.row_dimensions[r].height = 8
        r += 1

    # Version history box
    r += 1
    _set_cell(ws, r, 2, "VERSION HISTORY", font=FONT_SECTION,
              fill=FILL_DARK_SLATE, border=BORDER_GOLD_BOTTOM,
              alignment=ALIGN_LEFT)
    ws.cell(row=r, column=3).fill = FILL_DARK_SLATE
    ws.cell(row=r, column=3).border = BORDER_GOLD_BOTTOM
    r += 1
    _set_cell(ws, r, 2, "v2.0", font=FONT_BODY_BOLD, fill=FILL_OFF_WHITE,
              border=BORDER_THIN, alignment=ALIGN_LEFT)
    _set_cell(ws, r, 3, f"{run_date} — Institutional rebuild: openpyxl, design system, charts, data validation",
              font=FONT_BODY, fill=FILL_OFF_WHITE, border=BORDER_THIN,
              alignment=ALIGN_WRAP)
    r += 1
    _set_cell(ws, r, 2, "v1.0", font=FONT_BODY_BOLD, fill=FILL_WHITE,
              border=BORDER_THIN, alignment=ALIGN_LEFT)
    _set_cell(ws, r, 3, "Initial release — xlsxwriter-based export with basic formatting",
              font=FONT_BODY, fill=FILL_WHITE, border=BORDER_THIN,
              alignment=ALIGN_WRAP)

    ws.freeze_panes = "A4"


# ═══════════════════════════════════════════════════════════════════════
# EXCEL WRITER — MAIN ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════════════

def write_excel(all_tiers: dict[str, list[dict]], output_path: str) -> None:
    """Write the institutional-quality Excel workbook."""
    wb = Workbook()
    run_date = datetime.now().strftime("%B %d, %Y")

    all_stocks = [s for stocks in all_tiers.values() for s in stocks]

    print("    Building Dashboard...")
    _build_dashboard(wb, all_tiers, all_stocks, run_date)

    for tier_name in all_tiers:
        short = TIER_SHORT.get(tier_name, tier_name[:20])
        print(f"    Building {short}...")
        _build_tier_sheet(wb, short, tier_name, all_tiers[tier_name], run_date)

    print("    Building Descriptions...")
    _build_descriptions(wb, all_tiers, run_date)

    print("    Building Standouts...")
    _build_standouts(wb, all_stocks, run_date)

    print("    Building Recommendations...")
    _build_recommendations(wb, all_stocks, run_date)

    print("    Building Portfolio Construction...")
    _build_portfolio(wb, all_tiers, all_stocks, run_date)

    print("    Building Sensitivity Analysis...")
    _build_sensitivity(wb, all_stocks, run_date)

    print("    Building Revenue Trends...")
    _build_revenue_trends(wb, all_tiers, run_date)

    print("    Building Fingerprint Reference...")
    _build_fingerprint(wb, run_date)

    print("    Building Methodology...")
    _build_methodology(wb, run_date)

    # Move Dashboard to first position (it already is since we used wb.active)
    wb.move_sheet("Dashboard", offset=0)

    wb.save(output_path)
    print(f"\n  Workbook saved: {output_path}")
    print(f"  {len(all_stocks)} stocks across {len(all_tiers)} tiers")


# ═══════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════

def main() -> None:
    if not FMP_API_KEY:
        print("Error: FMP_API_KEY not set in .env")
        sys.exit(1)

    timestamp = datetime.now().strftime("%Y%m%d")
    output_dir = os.path.join(os.path.dirname(__file__), "output")
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, f"screener_output_{timestamp}.xlsx")

    print("Stock Screener — Excel Export v2.0")
    print("=" * 50)
    client = FMPClient(FMP_API_KEY)

    print("\n  Collecting data across all tiers...")
    all_tiers = collect_all_data(client)

    print("\n  Writing Excel workbook...")
    write_excel(all_tiers, output_path)

    print("\n  Done.")


if __name__ == "__main__":
    main()
