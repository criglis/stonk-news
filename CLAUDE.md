# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Stonk News is a Flask web app that shows stock/ETF price charts alongside financial news for a given ticker and date range. Prices are converted to EUR. Deployed on Render's free tier.

## Architecture

All application code lives in `stonk-news-app/`:

- **`app.py`** — Single-file Flask backend with three external data sources:
  - **Twelve Data API** (`TWELVE_DATA_KEY` env var) — daily price history
  - **Frankfurter API** (ECB, no key) — FX rates for converting prices to EUR
  - **Google News RSS** — financial news filtered to major sources (Reuters, CNBC, etc.)
- **`templates/index.html`** — Single-page frontend using Chart.js; calls `/api/analyze` and renders an interactive price chart with news items grouped by date/week
- **`Procfile`** — Gunicorn entry point for Render deployment

Key API endpoints:
- `GET /` — serves the SPA
- `GET /api/setup` — returns whether the Twelve Data API key is configured
- `GET /api/analyze?ticker=X&start=YYYY-MM-DD&end=YYYY-MM-DD` — returns price data, news, and stats as JSON

Periods >28 days are automatically resampled to weekly data.

## Development Commands

```bash
# Install dependencies
pip install -r stonk-news-app/requirements.txt

# Run locally (requires TWELVE_DATA_KEY env var)
cd stonk-news-app
TWELVE_DATA_KEY=<your-key> python app.py
# Runs on http://localhost:5000

# Production start (as configured in Procfile)
cd stonk-news-app
gunicorn app:app
```

## Environment Variables

- `TWELVE_DATA_KEY` — Required. Free API key from https://twelvedata.com (800 req/day)
- `PORT` — Optional. Defaults to 5000

## Root-Level HTML Files

The `.html` files in the repo root (e.g., `COPX_Apr-Jun2026.html`, `ICLN_chart.html`) are standalone chart artifacts, not part of the app.
