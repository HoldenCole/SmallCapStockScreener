# Stock Screener

A local Streamlit dashboard that screens for nano, small, and mid-cap US stocks in structural growth industries — space, quantum computing, AI infrastructure, photonics, defense tech, and semiconductors.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
# Edit .env and add your FMP API key
streamlit run app.py
```

## Features

- **Screener** — Filter by tier (Nano $50M–$300M, Small $300M–$2B, Breakout $2B–$15B) with composite scoring
- **Fingerprint Lab** — Compare candidates against reference stocks (LITE, COHR, AAOI, KTOS, RKLB, MVIS) at their pre-run inflection points
- **Deep Dive** — Single ticker analysis with revenue, margin, dilution, and fingerprint charts
- **Settings** — Adjust API key, scoring weights, and industry whitelist

## Data Source

All data from [Financial Modeling Prep](https://financialmodelingprep.com/) API. Responses are cached locally with 24-hour TTL.
