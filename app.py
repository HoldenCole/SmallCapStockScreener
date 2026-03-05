"""Stock Screener — Streamlit dashboard for finding structural growth stocks."""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px

from config import (
    FMP_API_KEY,
    TIERS,
    INDUSTRY_WHITELIST,
    DESCRIPTION_CHECK_SECTORS,
    DEFAULT_WEIGHTS,
    TOP_N_RESULTS,
)
from screener.fmp_client import FMPClient
from screener.filters import (
    apply_hard_filters,
    apply_sanity_filters,
    passes_quality_floor,
    _description_matches,
    score_stock,
)
from screener.fingerprint import (
    load_reference_stocks,
    compute_reference_averages,
    compute_ideal_ranges,
    compute_fingerprint_score,
)
from screener.utils import (
    fmt_market_cap,
    fmt_pct,
    compute_revenue_metrics,
    compute_dilution,
)

st.set_page_config(page_title="Stock Screener", page_icon="🔍", layout="wide")

# ---------------------------------------------------------------------------
# Session state defaults
# ---------------------------------------------------------------------------
if "api_key" not in st.session_state:
    st.session_state.api_key = FMP_API_KEY
if "weights" not in st.session_state:
    st.session_state.weights = dict(DEFAULT_WEIGHTS)
if "whitelist" not in st.session_state:
    st.session_state.whitelist = list(INDUSTRY_WHITELIST)
if "screener_results" not in st.session_state:
    st.session_state.screener_results = None
if "deep_dive_ticker" not in st.session_state:
    st.session_state.deep_dive_ticker = ""


def get_client() -> FMPClient:
    return FMPClient(st.session_state.api_key)


# ===================================================================
# TABS
# ===================================================================
tab_screener, tab_fingerprint, tab_deep_dive, tab_settings = st.tabs(
    ["🔍 Screener", "🧬 Fingerprint Lab", "📊 Deep Dive", "⚙️ Settings"]
)


# ===================================================================
# TAB 1 — SCREENER
# ===================================================================
with tab_screener:
    st.header("Stock Screener")

    tier_name = st.radio("Select tier", list(TIERS.keys()), horizontal=True)
    tier = TIERS[tier_name]

    if st.button("Run Screener", type="primary"):
        if not st.session_state.api_key:
            st.error("Set your FMP API key in the Settings tab first.")
        else:
            client = get_client()
            with st.spinner("Fetching screener results..."):
                raw = client.screen_stocks(tier["min"], tier["max"])

            if not raw:
                st.warning("No results returned from FMP. Check your API key or try again.")
            else:
                df = pd.DataFrame(raw)
                st.info(f"Fetched {len(df)} stocks. Applying filters...")

                if "description" not in df.columns:
                    df["description"] = ""

                # Pass 1: industry whitelist match
                whitelist_df = apply_hard_filters(df)

                # Pass 2: check descriptions for non-whitelist stocks
                # in tech-adjacent sectors (avoids fetching profiles
                # for banks, REITs, etc.)
                non_wl = df[
                    ~df["symbol"].isin(whitelist_df["symbol"])
                    & df["sector"].isin(DESCRIPTION_CHECK_SECTORS)
                ]
                desc_extras: list[str] = []
                if not non_wl.empty:
                    check_symbols = non_wl["symbol"].tolist()
                    desc_progress = st.progress(0)
                    for j, sym in enumerate(check_symbols):
                        desc_progress.progress(
                            (j + 1) / len(check_symbols),
                            text=f"Checking descriptions ({j+1}/{len(check_symbols)})",
                        )
                        profile = client.get_profile(sym)
                        if _description_matches(profile.get("description", "")):
                            desc_extras.append(sym)
                    desc_progress.empty()

                if desc_extras:
                    extra_df = df[df["symbol"].isin(desc_extras)]
                    df = pd.concat([whitelist_df, extra_df], ignore_index=True)
                    st.info(
                        f"{len(whitelist_df)} from industry whitelist "
                        f"+ {len(desc_extras)} from description keywords "
                        f"= {len(df)} total. Scoring..."
                    )
                else:
                    df = whitelist_df
                    st.info(f"{len(df)} stocks passed filters. Scoring...")

                if df.empty:
                    st.warning("No stocks passed the hard filters for this tier.")
                else:
                    # Fetch detailed metrics for surviving tickers
                    progress = st.progress(0)
                    results: list[dict] = []
                    tickers = df["symbol"].tolist()

                    skipped = 0
                    for i, ticker in enumerate(tickers):
                        progress.progress(
                            (i + 1) / len(tickers),
                            text=f"Analyzing {ticker} ({i+1}/{len(tickers)})",
                        )

                        row = df[df["symbol"] == ticker].iloc[0].to_dict()

                        # Fetch income statements for revenue/margin metrics
                        income = client.get_income_statements(ticker, quarters=8)
                        rev_metrics = compute_revenue_metrics(income)

                        # Fetch enterprise values for dilution
                        ev_data = client.get_enterprise_values(ticker, quarters=12)
                        dil_metrics = compute_dilution(ev_data)

                        # Insider ownership from shares float
                        # 100% - freeFloat% = insider + restricted shares
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
                            "revenue_acceleration_pct": rev_metrics[
                                "revenue_acceleration_pct"
                            ],
                            "market_cap_M": mkt_cap_m,
                        }

                        # Skip garbage data and stocks below quality floor
                        if not apply_sanity_filters(stock_metrics):
                            skipped += 1
                            continue
                        if not passes_quality_floor(stock_metrics):
                            skipped += 1
                            continue

                        composite = score_stock(
                            stock_metrics, st.session_state.weights
                        )
                        fingerprint = compute_fingerprint_score(stock_metrics)
                        combined = round(
                            composite * 0.6 + fingerprint * 0.4, 1
                        )

                        results.append(
                            {
                                "Ticker": ticker,
                                "Name": row.get("companyName", ""),
                                "Market Cap": fmt_market_cap(mkt_cap_m),
                                "Industry": row.get("industry", ""),
                                "Rev Growth": fmt_pct(
                                    rev_metrics["revenue_growth_pct"]
                                ),
                                "Gross Margin": fmt_pct(
                                    rev_metrics["gross_margin_pct"]
                                ),
                                "Dilution 3yr": fmt_pct(
                                    dil_metrics["dilution_3yr_pct"]
                                ),
                                "Insider %": fmt_pct(insider_pct),
                                "Composite": composite,
                                "Fingerprint": fingerprint,
                                "Combined": combined,
                            }
                        )

                    progress.empty()
                    if skipped:
                        st.caption(
                            f"Analyzed {len(tickers)} stocks, "
                            f"{len(results)} passed quality filters."
                        )
                    if not results:
                        st.warning("No stocks survived scoring.")
                    else:
                        results_df = (
                            pd.DataFrame(results)
                            .sort_values("Combined", ascending=False)
                            .head(TOP_N_RESULTS)
                        )
                        st.session_state.screener_results = results_df

    # Display results
    if st.session_state.screener_results is not None:
        results_df = st.session_state.screener_results
        st.dataframe(
            results_df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Combined": st.column_config.ProgressColumn(
                    "Score", min_value=0, max_value=100, format="%.0f"
                ),
                "Composite": st.column_config.NumberColumn(
                    "Composite", format="%.0f"
                ),
                "Fingerprint": st.column_config.NumberColumn(
                    "Fingerprint", format="%.0f"
                ),
            },
        )

        # Quick-dive selector
        selected = st.selectbox(
            "Select a ticker to deep dive",
            options=results_df["Ticker"].tolist(),
        )
        if st.button("Go to Deep Dive →"):
            st.session_state.deep_dive_ticker = selected
            st.info(f"Switch to the 📊 Deep Dive tab to view {selected}.")


# ===================================================================
# TAB 2 — FINGERPRINT LAB
# ===================================================================
with tab_fingerprint:
    st.header("🧬 Fingerprint Lab")
    st.caption(
        "Reference stocks and their metrics *before* they ran. "
        "The fingerprint score measures how similar a candidate looks to these winners."
    )

    refs = load_reference_stocks()
    ref_df = pd.DataFrame(refs)

    # Display table
    display_cols = [
        "ticker",
        "name",
        "pre_run_year",
        "theme",
        "pre_run_market_cap_M",
        "pre_run_revenue_growth_pct",
        "pre_run_gross_margin_pct",
        "pre_run_insider_ownership_pct",
        "pre_run_dilution_3yr_pct",
        "peak_return_from_entry_pct",
    ]
    st.dataframe(
        ref_df[display_cols].rename(
            columns={
                "ticker": "Ticker",
                "name": "Name",
                "pre_run_year": "Year",
                "theme": "Theme",
                "pre_run_market_cap_M": "Mkt Cap ($M)",
                "pre_run_revenue_growth_pct": "Rev Growth %",
                "pre_run_gross_margin_pct": "Gross Margin %",
                "pre_run_insider_ownership_pct": "Insider %",
                "pre_run_dilution_3yr_pct": "Dilution 3yr %",
                "peak_return_from_entry_pct": "Peak Return %",
            }
        ),
        use_container_width=True,
        hide_index=True,
    )

    # Radar chart
    st.subheader("Radar Comparison")
    categories = [
        "Revenue Growth",
        "Gross Margin",
        "Insider Ownership",
        "Low Dilution",
        "Market Cap Fit",
    ]

    fig = go.Figure()
    for ref in refs:
        # Normalize each metric to 0–100 for the radar
        values = [
            min(ref["pre_run_revenue_growth_pct"] / 95 * 100, 100),  # 95 is max in set
            min(ref["pre_run_gross_margin_pct"] / 50 * 100, 100),
            min(ref["pre_run_insider_ownership_pct"] / 30 * 100, 100),
            max(0, 100 - ref["pre_run_dilution_3yr_pct"] * 4),  # invert
            min(ref["pre_run_market_cap_M"] / 1200 * 100, 100),
        ]
        fig.add_trace(
            go.Scatterpolar(
                r=values + [values[0]],  # close the polygon
                theta=categories + [categories[0]],
                name=ref["ticker"],
                fill="toself",
                opacity=0.3,
            )
        )
    fig.update_layout(
        polar=dict(radialaxis=dict(visible=True, range=[0, 100])),
        showlegend=True,
        height=500,
    )
    st.plotly_chart(fig, use_container_width=True)

    # Ideal ranges
    st.subheader("Derived Ideal Ranges")
    ideal = compute_ideal_ranges(refs)
    avg = compute_reference_averages(refs)
    range_data = []
    for metric, (lo, hi) in ideal.items():
        label = metric.replace("_pct", " %").replace("_M", " ($M)").replace("_", " ").title()
        range_data.append(
            {"Metric": label, "Min": lo, "Max": hi, "Average": round(avg[metric], 1)}
        )
    st.dataframe(pd.DataFrame(range_data), use_container_width=True, hide_index=True)


# ===================================================================
# TAB 3 — DEEP DIVE
# ===================================================================
with tab_deep_dive:
    st.header("📊 Stock Deep Dive")

    ticker = st.text_input(
        "Ticker", value=st.session_state.deep_dive_ticker
    ).upper().strip()

    if ticker and st.button("Analyze", type="primary"):
        if not st.session_state.api_key:
            st.error("Set your FMP API key in the Settings tab first.")
        else:
            client = get_client()

            with st.spinner(f"Fetching data for {ticker}..."):
                profile = client.get_profile(ticker)
                income = client.get_income_statements(ticker, quarters=8)
                ev_data = client.get_enterprise_values(ticker, quarters=12)
                float_data = client.get_shares_float(ticker)

            if not profile:
                st.error(f"Could not find profile for {ticker}.")
            else:
                # Company info
                st.subheader(f"{profile.get('companyName', ticker)}")
                col1, col2, col3 = st.columns(3)
                mkt_cap_m = profile.get("marketCap", 0) / 1_000_000
                col1.metric("Market Cap", fmt_market_cap(mkt_cap_m))
                col2.metric("Sector", profile.get("sector", "N/A"))
                col3.metric("Industry", profile.get("industry", "N/A"))

                st.markdown(
                    f"**Description:** {profile.get('description', 'N/A')[:500]}"
                )
                st.divider()

                # Compute metrics
                rev_metrics = compute_revenue_metrics(income)
                dil_metrics = compute_dilution(ev_data)
                free_float = float_data.get("freeFloat")
                insider_pct = None
                if isinstance(free_float, (int, float)) and 0 < free_float <= 100:
                    insider_pct = round(100.0 - free_float, 1)

                # Key metrics row
                m1, m2, m3, m4 = st.columns(4)
                m1.metric("Revenue Growth YoY", fmt_pct(rev_metrics["revenue_growth_pct"]))
                m2.metric("Gross Margin", fmt_pct(rev_metrics["gross_margin_pct"]))
                m3.metric("Dilution 3yr", fmt_pct(dil_metrics["dilution_3yr_pct"]))
                m4.metric("Insider Ownership", fmt_pct(insider_pct))

                # Charts
                st.divider()
                chart_left, chart_right = st.columns(2)

                # Revenue trend
                with chart_left:
                    if rev_metrics["quarterly_revenue"]:
                        rev_df = pd.DataFrame(
                            {
                                "Date": rev_metrics["quarterly_dates"],
                                "Revenue": rev_metrics["quarterly_revenue"],
                            }
                        )
                        fig_rev = px.bar(
                            rev_df, x="Date", y="Revenue", title="Quarterly Revenue"
                        )
                        st.plotly_chart(fig_rev, use_container_width=True)

                # Gross margin trend
                with chart_right:
                    if rev_metrics["quarterly_gross_margin"]:
                        gm_df = pd.DataFrame(
                            {
                                "Date": rev_metrics["quarterly_dates"],
                                "Gross Margin %": rev_metrics[
                                    "quarterly_gross_margin"
                                ],
                            }
                        )
                        fig_gm = px.line(
                            gm_df,
                            x="Date",
                            y="Gross Margin %",
                            title="Gross Margin Trend",
                            markers=True,
                        )
                        st.plotly_chart(fig_gm, use_container_width=True)

                chart_left2, chart_right2 = st.columns(2)

                # Shares outstanding
                with chart_left2:
                    if dil_metrics["shares_outstanding"]:
                        shares_df = pd.DataFrame(
                            {
                                "Date": dil_metrics["shares_dates"],
                                "Shares": dil_metrics["shares_outstanding"],
                            }
                        )
                        fig_shares = px.area(
                            shares_df,
                            x="Date",
                            y="Shares",
                            title="Shares Outstanding",
                        )
                        st.plotly_chart(fig_shares, use_container_width=True)

                # Fingerprint gauge
                with chart_right2:
                    stock_metrics = {
                        "revenue_growth_pct": rev_metrics["revenue_growth_pct"],
                        "gross_margin_pct": rev_metrics["gross_margin_pct"],
                        "dilution_3yr_pct": dil_metrics["dilution_3yr_pct"],
                        "insider_ownership_pct": insider_pct,
                        "market_cap_M": mkt_cap_m,
                    }
                    fp = compute_fingerprint_score(stock_metrics)
                    composite = score_stock(
                        {
                            **stock_metrics,
                            "revenue_acceleration_pct": rev_metrics[
                                "revenue_acceleration_pct"
                            ],
                        },
                        st.session_state.weights,
                    )

                    fig_gauge = go.Figure(
                        go.Indicator(
                            mode="gauge+number",
                            value=fp,
                            title={"text": "Fingerprint Score"},
                            gauge={
                                "axis": {"range": [0, 100]},
                                "bar": {"color": "royalblue"},
                                "steps": [
                                    {"range": [0, 40], "color": "#ffcccc"},
                                    {"range": [40, 70], "color": "#ffffcc"},
                                    {"range": [70, 100], "color": "#ccffcc"},
                                ],
                            },
                        )
                    )
                    fig_gauge.update_layout(height=300)
                    st.plotly_chart(fig_gauge, use_container_width=True)
                    st.metric("Composite Score", f"{composite:.0f} / 100")


# ===================================================================
# TAB 4 — SETTINGS
# ===================================================================
with tab_settings:
    st.header("⚙️ Settings")

    # API Key
    st.subheader("FMP API Key")
    new_key = st.text_input(
        "API Key",
        value=st.session_state.api_key,
        type="password",
    )
    if new_key != st.session_state.api_key:
        st.session_state.api_key = new_key
        st.success("API key updated for this session.")

    st.divider()

    # Scoring weights
    st.subheader("Scoring Weights")
    st.caption("Adjust how much each signal contributes to the composite score.")

    w = st.session_state.weights
    w["revenue_growth"] = st.slider("Revenue Growth", 0.0, 1.0, w["revenue_growth"], 0.05)
    w["gross_margin"] = st.slider("Gross Margin", 0.0, 1.0, w["gross_margin"], 0.05)
    w["dilution"] = st.slider("Dilution", 0.0, 1.0, w["dilution"], 0.05)
    w["insider_ownership"] = st.slider(
        "Insider Ownership", 0.0, 1.0, w["insider_ownership"], 0.05
    )
    w["revenue_acceleration"] = st.slider(
        "Revenue Acceleration", 0.0, 1.0, w["revenue_acceleration"], 0.05
    )

    total_w = sum(w.values())
    if abs(total_w - 1.0) > 0.01:
        st.warning(f"Weights sum to {total_w:.2f} — ideally they should sum to 1.0.")
    else:
        st.success("Weights sum to 1.0 ✓")

    st.divider()

    # Industry whitelist editor
    st.subheader("Industry Whitelist")
    whitelist_text = st.text_area(
        "One industry per line",
        value="\n".join(st.session_state.whitelist),
        height=200,
    )
    parsed = [line.strip() for line in whitelist_text.split("\n") if line.strip()]
    if parsed != st.session_state.whitelist:
        st.session_state.whitelist = parsed
        st.success(f"Whitelist updated: {len(parsed)} industries.")
