# Stock Screener — Claude Code Project Spec

## Project Overview

Build a local Streamlit dashboard that screens for nano/small/mid-cap stocks in structural growth industries (space, quantum, AI infrastructure, photonics, defense tech, semiconductors). The screener uses a "fingerprint" derived from studying successful stocks like LITE, COHR, and AAOI *before* their major runs, and applies those patterns to find today's candidates.

---

## Tech Stack

- **Python 3.11+**
- **Streamlit** — local dashboard UI
- **Financial Modeling Prep (FMP) API** — data source
- **pandas** — data wrangling
- **plotly** — charts
- **requests** — API calls

---

## Folder Structure

```
stock-screener/
├── app.py                  # Streamlit entry point
├── config.py               # API key, thresholds, whitelists
├── screener/
│   ├── __init__.py
│   ├── fmp_client.py       # All FMP API calls
│   ├── filters.py          # Screening logic & scoring
│   ├── fingerprint.py      # Success pattern analysis
│   └── utils.py            # Helpers, formatting
├── data/
│   └── reference_stocks.json   # LITE, COHR, AAOI + others with pre-run metrics
├── requirements.txt
└── README.md
```

---

## Pages / Tabs in the App

### 1. 🔍 Screener
The main screen. Three modes selectable via radio button or tabs:

**Nano Cap** — Market cap $50M–$300M  
**Small Cap** — Market cap $300M–$2B  
**Breakout Tier** — Market cap $2B–$15B (LITE/COHR/AAOI tier — already proven, still moving)

Each mode shows a scored, sortable table of results.

### 2. 🧬 Fingerprint Lab
Analyze what LITE, COHR, AAOI, and similar names had in common *before* they ran. Display:
- Their metrics at the time of the inflection (revenue growth, margin, market cap, insider ownership, share count trend)
- A radar/spider chart comparing them
- A derived scoring rubric from those shared traits

### 3. 📊 Stock Deep Dive
Click any ticker in the screener → get a detailed view:
- Revenue & gross margin trend (last 8 quarters)
- Shares outstanding over time (dilution chart)
- Insider ownership history
- FMP profile: description, sector, industry
- "Fingerprint match score" — how similar to the pre-run profiles of LITE/COHR/AAOI

### 4. ⚙️ Settings
- Enter/update FMP API key
- Adjust screening thresholds
- Edit industry whitelist

---

## Screening Signals & Logic

### Hard Filters (must pass to appear)
```python
# Industry whitelist — FMP sector/industry strings
INDUSTRY_WHITELIST = [
    "Aerospace & Defense",
    "Electronic Components",
    "Semiconductors",
    "Semiconductor Equipment & Materials",
    "Communication Equipment",
    "Software - Infrastructure",
    "Information Technology Services",
    "Scientific & Technical Instruments",
    "Specialty Industrial Machinery",
]

# Keyword whitelist for company descriptions (fallback)
DESCRIPTION_KEYWORDS = [
    "quantum", "photon", "laser", "lidar", "satellite", "space", "rocket",
    "drone", "autonomous", "AI", "artificial intelligence", "machine learning",
    "fiber optic", "optical", "hypersonic", "radar", "sensor fusion",
    "edge computing", "data center", "networking", "wireless infrastructure"
]

EXCLUDED_SECTORS = ["Biotechnology", "Pharmaceuticals", "Drug Manufacturers"]
COUNTRY = "US"
```

### Scored Signals (0–100 composite score)

| Signal | Weight | Logic |
|--------|--------|-------|
| Revenue growth YoY | 30% | >50% = full points, scales down |
| Gross margin | 20% | >40% = full points |
| Share dilution (3yr) | 20% | Shares outstanding change: <5% growth = full points, penalize heavy issuers |
| Insider ownership | 15% | >10% = full points |
| Revenue trend (acceleration) | 15% | Is growth rate itself increasing? |

### Display Columns (not filters, just shown)
- Short float %
- Institutional ownership %
- Cash / market cap ratio (runway proxy)
- Next earnings date

---

## Fingerprint Lab — Reference Stocks

Pre-populate `data/reference_stocks.json` with these tickers and their *pre-run* metrics. The app will also fetch current metrics for comparison.

```json
{
  "reference_stocks": [
    {
      "ticker": "LITE",
      "name": "Lumentum Holdings",
      "industry": "Photonics / Optical Components",
      "pre_run_year": 2017,
      "theme": "Data center optical interconnects",
      "pre_run_market_cap_M": 1200,
      "pre_run_revenue_growth_pct": 38,
      "pre_run_gross_margin_pct": 44,
      "pre_run_insider_ownership_pct": 8,
      "pre_run_dilution_3yr_pct": 4,
      "peak_return_from_entry_pct": 850
    },
    {
      "ticker": "COHR",
      "name": "Coherent Corp (formerly II-VI)",
      "industry": "Photonics / Compound Semiconductors",
      "pre_run_year": 2016,
      "theme": "Optical networking + vertical integration",
      "pre_run_market_cap_M": 900,
      "pre_run_revenue_growth_pct": 22,
      "pre_run_gross_margin_pct": 50,
      "pre_run_insider_ownership_pct": 12,
      "pre_run_dilution_3yr_pct": 6,
      "peak_return_from_entry_pct": 600
    },
    {
      "ticker": "AAOI",
      "name": "Applied Optoelectronics",
      "industry": "Fiber Optic Transceivers",
      "pre_run_year": 2017,
      "theme": "Hyperscaler data center buildout",
      "pre_run_market_cap_M": 300,
      "pre_run_revenue_growth_pct": 95,
      "pre_run_gross_margin_pct": 38,
      "pre_run_insider_ownership_pct": 20,
      "pre_run_dilution_3yr_pct": 3,
      "peak_return_from_entry_pct": 1200
    },
    {
      "ticker": "KTOS",
      "name": "Kratos Defense & Security",
      "industry": "Aerospace & Defense",
      "pre_run_year": 2019,
      "theme": "Drone warfare + affordable attritable aircraft",
      "pre_run_market_cap_M": 700,
      "pre_run_revenue_growth_pct": 18,
      "pre_run_gross_margin_pct": 25,
      "pre_run_insider_ownership_pct": 5,
      "pre_run_dilution_3yr_pct": 8,
      "peak_return_from_entry_pct": 500
    },
    {
      "ticker": "RKLB",
      "name": "Rocket Lab USA",
      "industry": "Space Launch / Satellites",
      "pre_run_year": 2023,
      "theme": "Small sat launch + space systems vertical integration",
      "pre_run_market_cap_M": 1200,
      "pre_run_revenue_growth_pct": 55,
      "pre_run_gross_margin_pct": 28,
      "pre_run_insider_ownership_pct": 30,
      "pre_run_dilution_3yr_pct": 15,
      "peak_return_from_entry_pct": 400
    },
    {
      "ticker": "MVIS",
      "name": "MicroVision",
      "industry": "LiDAR / Sensing",
      "pre_run_year": 2021,
      "theme": "Autonomous vehicle sensor infrastructure",
      "pre_run_market_cap_M": 150,
      "pre_run_revenue_growth_pct": 12,
      "pre_run_gross_margin_pct": 35,
      "pre_run_insider_ownership_pct": 6,
      "pre_run_dilution_3yr_pct": 25,
      "peak_return_from_entry_pct": 700
    }
  ]
}
```

---

## FMP API Calls Needed

```python
# Screener endpoint — main query
GET /v3/stock-screener
  params: marketCapMoreThan, marketCapLessThan, sector, country, isActivelyTrading, limit

# Fundamentals
GET /v3/income-statement/{ticker}?limit=8      # Revenue, gross margin, quarterly
GET /v3/shares_float/{ticker}                  # Short float
GET /v3/institutional-holder/{ticker}          # Institutional ownership
GET /v3/insider-roaster-statistic/{ticker}     # Insider ownership %
GET /v3/historical-shares-outstanding/{ticker} # Dilution tracking
GET /v3/profile/{ticker}                       # Description, sector, industry, market cap
```

---

## Scoring — Fingerprint Match

For each screened stock, compare its current metrics against the *average* of the reference stocks at their pre-run point:

```python
def fingerprint_score(stock_metrics, reference_avg):
    """
    Returns 0-100 score based on similarity to pre-run fingerprint.
    High score = looks like LITE/COHR/AAOI before they ran.
    """
    scores = []
    
    # Revenue growth proximity
    scores.append(proximity_score(stock_metrics['rev_growth'], reference_avg['rev_growth'], ideal_range=(20, 100)))
    
    # Gross margin proximity
    scores.append(proximity_score(stock_metrics['gross_margin'], reference_avg['gross_margin'], ideal_range=(25, 55)))
    
    # Dilution — lower is better
    scores.append(dilution_score(stock_metrics['dilution_3yr']))
    
    # Insider ownership
    scores.append(proximity_score(stock_metrics['insider_pct'], reference_avg['insider_pct'], ideal_range=(5, 40)))
    
    # Market cap in right zone
    scores.append(cap_zone_score(stock_metrics['market_cap_M']))
    
    return round(sum(scores) / len(scores), 1)
```

---

## Initial Prompts for Claude Code

Paste these in sequence into Claude Code:

### Prompt 1 — Project Setup
```
Create a new Python project called stock-screener with the following structure:
[paste folder structure from spec]

Initialize a virtual environment, create requirements.txt with:
streamlit, pandas, plotly, requests, python-dotenv

Create a .env.example file with FMP_API_KEY=your_key_here
Create config.py that loads the API key from .env and defines the industry whitelist, 
description keywords, excluded sectors, and scoring weights listed in the spec.
```

### Prompt 2 — FMP Client
```
Create screener/fmp_client.py. It should have a class FMPClient initialized with an API key.
Implement these methods:
- screen_stocks(min_cap, max_cap, limit=200) → calls /v3/stock-screener
- get_income_statements(ticker, quarters=8) → quarterly revenue + gross margin
- get_shares_outstanding(ticker) → historical share count
- get_insider_ownership(ticker) → % insider held
- get_profile(ticker) → sector, industry, description, market cap
- get_short_float(ticker) → short float %

All methods should handle errors gracefully, return empty dict/list on failure, 
and include a 0.2s rate limit delay between calls.
```

### Prompt 3 — Filters & Scoring
```
Create screener/filters.py. Implement:
1. apply_hard_filters(df) — filters out biotech, non-US, inactive stocks, and keeps only 
   companies matching the industry whitelist OR description keywords from config.py
2. score_stock(metrics_dict) — returns a composite 0-100 score based on the weights in config.py
3. fingerprint_score(stock_metrics, reference_averages) — compares stock to pre-run reference 
   averages and returns 0-100 similarity score
```

### Prompt 4 — Main App
```
Create app.py as a Streamlit app with 4 tabs: Screener, Fingerprint Lab, Deep Dive, Settings.

Screener tab:
- Radio buttons: Nano (<$300M), Small ($300M-$2B), Breakout ($2B-$15B)
- Run Screener button
- Results in a sortable st.dataframe with columns: Ticker, Name, Market Cap, Industry, 
  Revenue Growth, Gross Margin, Dilution 3yr, Insider %, Composite Score, Fingerprint Score
- Clicking a row navigates to Deep Dive tab for that ticker

Fingerprint Lab tab:
- Load data/reference_stocks.json
- Show a table of reference stocks with their pre-run metrics
- Show a plotly radar chart comparing all reference stocks across 5 metrics
- Show derived "ideal range" for each metric

Deep Dive tab:
- Ticker input at top
- 4 charts side by side: Revenue trend, Gross margin trend, Shares outstanding, Insider %
- Fingerprint score with a gauge chart
- Company description and sector/industry from FMP profile

Settings tab:
- API key input (saved to .env)
- Sliders for adjusting score weights
```

### Prompt 5 — Reference Data & Polish
```
Create data/reference_stocks.json with the reference stock data from the spec.
Create screener/fingerprint.py that:
1. Loads reference_stocks.json
2. Computes average pre-run metrics across all reference stocks  
3. Computes per-stock and average fingerprint "fingerprints" for radar chart
4. Exports compute_fingerprint_score(stock_metrics) function

Then create README.md with setup instructions:
- pip install -r requirements.txt
- Copy .env.example to .env and add FMP API key
- streamlit run app.py
```

---

## Notes for Development

- FMP free tier is rate-limited. When screening, fetch the screener list first, apply hard filters in-memory, *then* fetch detailed metrics only for surviving tickers.
- Cache API responses in a local `data/cache/` folder with a 24hr TTL to avoid hammering the API during development.
- The fingerprint scores are directional, not precise — frame them in the UI as "similarity signal" not a buy recommendation.
- For the Breakout tier ($2B–$15B), the fingerprint is less about current size and more about whether growth is *re-accelerating* — weight revenue acceleration more heavily for that mode.

---

## FMP Plan Recommendation

**Starter plan (~$30/mo)** covers everything needed here. You'll need:
- Stock screener endpoint ✓
- Fundamental data (income statements) ✓  
- Shares outstanding history ✓
- Insider ownership ✓
- Institutional holders ✓

Sign up at: https://financialmodelingprep.com/developer/docs
