# CLAUDE.md — Stock Screener Project Briefing

This file tells Claude Code everything it needs to know about this project before touching any code. Read this fully before making any changes.

---

## What This Project Is

A **local Streamlit dashboard** that screens for nano, small, and mid-cap US stocks in structural growth industries — space, quantum computing, AI infrastructure, photonics, defense tech, semiconductors, and adjacent "choke point" industries.

The goal is not to find any small cap. The goal is to find stocks that look like **LITE (Lumentum), COHR (Coherent), and AAOI (Applied Optoelectronics) before they ran** — companies operating at critical junctures in fast-growing supply chains, with real revenue growth, low dilution, and founder/insider alignment.

We study what those winners had in common at their inflection point and build a "fingerprint" scoring system to find today's equivalents.

---

## What We Are NOT Building

- We are NOT building a general-purpose stock screener
- We are NOT screening biotech, pharma, or drug manufacturers — ever
- We are NOT optimizing for dividend stocks, value plays, or anything defensive
- We are NOT building something that needs a server or deployment — this runs locally only
- We are NOT trying to find already-discovered large caps — the whole point is early stage

---

## The Three Screener Tiers

| Tier | Market Cap Range | What We're Looking For |
|------|-----------------|----------------------|
| **Nano** | $50M – $300M | Earliest stage. High risk, highest upside. Must pass strict dilution and insider filters. |
| **Small** | $300M – $2B | The sweet spot. Proven enough to have real revenue, small enough to 10x. |
| **Breakout** | $2B – $15B | The LITE/COHR/AAOI tier. Already validated, but growth re-accelerating. Bigger position sizes make sense here. |

---

## The Fingerprint System

The core intellectual property of this screener. We have a set of reference stocks — companies that went on to 4–12x from a specific entry point. We know their metrics *before they ran*:

- Revenue growth rate at the time
- Gross margin at the time
- How much they had diluted shares over the prior 3 years
- Insider ownership percentage
- Market cap at inflection

The screener computes a **Fingerprint Score (0–100)** for every candidate — how similar does this company look today compared to those reference stocks at their best entry point?

High fingerprint score ≠ guaranteed win. It means: structurally, this company looks like the ones that worked.

**Reference stocks currently in the system:**
- LITE (Lumentum) — photonics, data center optical interconnects, ~2017
- COHR (Coherent/II-VI) — compound semiconductors, optical networking, ~2016
- AAOI (Applied Optoelectronics) — fiber optic transceivers, hyperscaler buildout, ~2017
- KTOS (Kratos Defense) — drone warfare, attritable aircraft, ~2019
- RKLB (Rocket Lab) — small sat launch, space systems, ~2023

---

## Data Source: Financial Modeling Prep (FMP)

All data comes from the FMP API. API key is stored in `.env` as `FMP_API_KEY`.

**Key principle:** FMP has rate limits. Always:
1. Run the screener endpoint first to get a broad list
2. Apply hard filters **in memory** using data already returned
3. Only then fetch detailed metrics (income statements, insider data, etc.) for surviving tickers
4. Cache all API responses in `data/cache/` with 24-hour TTL

Never make detailed API calls for every ticker in a broad list. That will burn rate limits fast.

---

## Scoring System

### Hard Filters (disqualify immediately)
- Sector contains "Biotech" or "Pharma" → OUT
- Country is not US → OUT
- Not actively trading → OUT
- Industry not in whitelist AND description doesn't contain keyword → OUT

### Composite Score (0–100, weighted)
| Signal | Weight | Notes |
|--------|--------|-------|
| Revenue growth YoY | 30% | Higher is better. >50% = near max points. |
| Gross margin | 20% | >40% is ideal. Weeds out commodity businesses. |
| Share dilution (3yr) | 20% | Lower is better. <5% growth in shares = full points. Heavy issuers penalized hard. |
| Insider ownership % | 15% | >10% = full points. Founders with skin in the game. |
| Revenue acceleration | 15% | Is growth rate itself increasing quarter over quarter? |

### Fingerprint Score (0–100, separate)
How similar is this stock to the reference stocks *at their pre-run point*. Computed by `screener/fingerprint.py`.

---

## File Structure

```
stock-screener/
├── app.py                  # Streamlit entry point — 4 tabs
├── config.py               # API key, thresholds, whitelists, weights
├── screener/
│   ├── __init__.py
│   ├── fmp_client.py       # All FMP API calls, caching, rate limiting
│   ├── filters.py          # Hard filters + composite scoring
│   ├── fingerprint.py      # Fingerprint scoring vs reference stocks
│   └── utils.py            # Formatting helpers
├── data/
│   ├── reference_stocks.json   # Pre-run metrics for LITE, COHR, AAOI, etc.
│   └── cache/                  # Auto-created. API response cache (24hr TTL).
├── .env                    # NOT committed. Contains FMP_API_KEY.
├── .env.example            # Committed. Template for .env.
├── requirements.txt
├── README.md
└── CLAUDE.md               # This file.
```

---

## App Structure (4 Tabs)

### Tab 1: 🔍 Screener
Main table. Radio button selects tier (Nano / Small / Breakout). Run button triggers the pipeline. Results are a sortable dataframe. Clicking a row loads that ticker in the Deep Dive tab.

### Tab 2: 🧬 Fingerprint Lab
Shows all reference stocks and their pre-run metrics. Radar/spider chart comparing them. Derived "ideal range" for each signal. This is the "research" tab — helps the user understand why the scoring is set the way it is.

### Tab 3: 📊 Deep Dive
Single ticker view. Revenue trend, margin trend, shares outstanding history, insider %. Plus fingerprint score gauge. Company description from FMP.

### Tab 4: ⚙️ Settings
API key entry. Score weight sliders. Industry whitelist editor.

---

## Industry Whitelist (from config.py)

These are the FMP sector/industry strings we accept:

```
Aerospace & Defense
Electronic Components
Semiconductors
Semiconductor Equipment & Materials
Communication Equipment
Software - Infrastructure
Information Technology Services
Scientific & Technical Instruments
Specialty Industrial Machinery
```

Description keyword fallback (for companies FMP mis-tags):
`quantum, photon, laser, lidar, satellite, space, rocket, drone, autonomous, AI, artificial intelligence, machine learning, fiber optic, optical, hypersonic, radar, sensor fusion, edge computing, data center, networking, wireless infrastructure`

---

## Code Style & Conventions

- Use type hints everywhere
- All FMP calls go through `FMPClient` — never call `requests` directly from app.py or filters.py
- Scores are always floats 0.0–100.0
- Market caps are always in **millions USD** (so $1B = 1000.0)
- Keep Streamlit state in `st.session_state` — don't use global variables
- If an API call fails, log the error and return an empty result — never crash the app
- Comments should explain *why*, not *what*

---

## What "Good" Looks Like

A stock that should score well:
- US-listed, $100M–$1B market cap
- Operates in space, photonics, quantum, AI infrastructure, or defense tech
- Revenue growing 30%+ YoY, ideally accelerating
- Gross margin >35%
- Shares outstanding have grown <10% over 3 years (they're not diluting to survive)
- Insiders own >8% of the company
- Founders are still involved

A stock that should be filtered out immediately:
- Any biotech or pharma
- Revenue shrinking or flat
- Shares outstanding up 40%+ in 3 years (serial diluter)
- Foreign-listed ADR
- OTC/pink sheet (not actively traded on major exchange)

---

## Do Not

- Do not add biotech or healthcare to the whitelist, no matter what
- Do not make the UI complicated — it should feel like a tool, not a product
- Do not store secrets in code — API key always from `.env`
- Do not make API calls synchronously in the UI thread for large batches — show a progress bar
- Do not remove the caching layer — it protects rate limits during development
