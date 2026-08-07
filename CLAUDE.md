# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`ticker-change` is a single-process Flask equity-research dashboard. Enter a
ticker and get six connected views (price table, risk analytics, market
positioning, live microstructure + options Greeks, momentum screening, AI
analyst report) built from free-tier data sources (`yfinance`, Finnhub, FMP,
SEC EDGAR) that all degrade gracefully when unconfigured or rate-limited.

There is no frontend build step — Tailwind is loaded from the CDN and charts
are server-rendered Plotly HTML embedded directly in Jinja templates.

## Commands

```bash
# Setup
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Run (dev, auto-reload) — http://127.0.0.1:5001
python3 app.py

# Run (prod-like)
gunicorn app:app --bind 0.0.0.0:5001 --workers 2

# Train the ML signal model (optional; without it the ML section is omitted)
python ml.py train AAPL MSFT NVDA SPY ...

# Warm the S3 option-chain cache manually (same script the hourly GH Action runs)
python warm_s3_cache.py
```

There is no test suite, linter, or type checker configured in this repo —
don't invent one unless asked. Verify changes by running the app and hitting
the relevant route/`/api/*` endpoint.

Deploying (Fly.io is the live target; confirm with the user before running):
`fly deploy --ha=false` — single-instance only, because SQLite has no
multi-writer story here.

## Architecture

### Everything lives in `app.py` (~3,600 lines)

Routes, page data-assembly functions, chart generation (Plotly), options
pricing (in-house Black-Scholes Greeks), GEX/dealer-positioning math, and the
momentum backtest are all in this one file. Each page route (`/stock`,
`/analytics`, `/positioning`, `/live`, `/momentum`, `/ai-summary`) has a
matching `get_*` function that assembles a data dict passed straight to the
Jinja template, plus a JSON twin under `/api/*` that returns the same data
minus embedded chart HTML. When adding a page feature, expect to touch three
places: the `get_*` builder in `app.py`, the route, and the template.

Supporting modules:
- `db.py` — SQLite layer (`stocks.db`): `daily_prices` cache, generic
  `api_cache` (provider/AI response cache with TTL), `app_settings`
  (user-entered API keys from `/settings`), and a `try_claim_lock` helper for
  cross-worker locking.
- `providers.py` — Finnhub / FMP / SEC EDGAR clients plus AI-provider key
  resolution. Every provider call returns `None` on failure instead of
  raising; nothing here should ever take the dashboard down.
- `signals.py` — deterministic multi-factor "Buyer Signals" composite
  (trend/momentum/GEX/Monte-Carlo/volatility/tail-risk/valuation/ML,
  weighted, 0–100). Pure function of already-computed analytics, no LLM.
- `ml.py` — gradient-boosted (LightGBM, falls back to sklearn) Buy/Hold/Sell
  model trained offline via `python ml.py train ...` on triple-barrier labels;
  serialized to `model.pkl` (gitignored). Predictions older than 30 days are
  suppressed.
- `ai.py` — LLM analyst report generation (standard/comprehensive/options
  variants), multi-provider fallback chain, results cached in `api_cache`.
- `glossary.py` — single source of truth for every metric's tooltip text and
  `/glossary` page entry (`GLOSSARY` dict, injected as a Jinja global in
  `app.py`).
- `s3_cache.py` — optional S3-backed cache specifically for option chains
  (see below); no-ops to the SQLite `api_cache` fallback when
  `S3_CACHE_BUCKET` is unset.

### Caching, layered for a tight free-tier budget

1. **In-process memo** (`_PRICE_MEMO` in `app.py`) — collapses duplicate
   price look-ups within/across nearby requests (45s TTL).
2. **SQLite `daily_prices`** — 1h freshness (`db.is_fresh`); refreshed from
   `yfinance` with retry/backoff on a miss.
3. **Stale-while-error** — if a refresh fails but older rows exist, those are
   served instead of an error.
4. **`api_cache`** — generic 24h (provider) / 12h (AI) TTL cache keyed by
   `(provider, key)`.
5. **Option chains get their own path**: `get_cached_chain`/
   `get_cached_expirations` in `app.py` read S3 first (`s3_cache.py`, 4h
   TTL, one JSON object per ticker/expiration, survives redeploys and is
   shared across instances), then SQLite `api_cache`, then a live
   `yfinance` fetch (written back to both), falling back to stale S3 →
   stale SQLite → `None`. A GitHub Action (`.github/workflows/warm-cache.yml`)
   runs `warm_s3_cache.py` hourly during market hours to keep this warm.

When changing fetch/cache behavior, preserve the "always render something"
contract — every layer that can fail should fall through to the next rather
than raising.

### UI mode plumbing (light / dark / excel)

Three-state theme stored in `localStorage` (pre-paint, anti-flash) and
mirrored to a `ui_mode` cookie so Flask can branch structurally. The
`inject_ui_mode` context processor in `app.py` exposes `excel_mode` to every
template. CSS-only "Excel" paint lives in `static/excel-mode.css` (keyed off
`html.excel`); structural conversion (cards → spreadsheet-style grids) uses
Jinja macros in `templates/_excel.html`. See
`docs/EXCEL_MODE_ARCHITECTURE.md` for the full design (this doc is
gitignored — local reference only, not shipped with the repo). The `xlfmt`
Jinja filter in `app.py` renders negatives in accounting-style parens for
Excel mode.

`templates/_macros.html` provides the `metric(label, key)` macro used
throughout for the `(i)` tooltip that links every stat to its `/glossary`
entry — reuse it instead of hand-rolling label markup for new metrics.

### `docs/` is gitignored

`docs/PROJECT_CONTEXT.md` and `docs/ORCHESTRATION.md` describe a **planned,
not-yet-built** analytics extension (DuckDB warehouse, dbt models, an
`orchestrate.py` ELT pipeline, FRED/macro enrichment) — none of that code
exists in this repo yet (no `analytics/`, `orchestrate.py`, or `dashboards/`
directories). Don't assume it's implemented; treat those docs as design
notes for future work, not current architecture.

### Deployment

Fly.io is the live target (`fly.toml`): a `stocks_data` volume mounted at
`/data`, single machine (`fly deploy --ha=false`) because SQLite can't handle
multi-writer. `fly.toml` sets `DB_PATH=/data/stocks.db`; `db.py` reads
`DB_PATH` from the environment (falling back to `stocks.db` for local dev),
so the volume mount is what makes `stocks.db` survive redeploys.
`Dockerfile`/`Procfile`/`render.yaml` also exist for Render as an alternative
target. API keys are optional everywhere and can be set via environment,
`.env`, or the `/settings` page (settings-page values override env vars); see
`.env.example` for the full list.
