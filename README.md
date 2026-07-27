# ticker/change

A Flask-based equity research terminal that combines price-change analytics,
quantitative risk modelling, market-positioning intelligence, options Greeks,
momentum screening, and AI-generated analyst reports — all from free data
sources with graceful degradation at every layer.

Enter a ticker and navigate six connected views:

| View | Route | What it does |
| --- | --- | --- |
| **Price Table** | `/stock` | Multi-period price-change table with an interactive candlestick chart |
| **Risk Analytics** | `/analytics` | Quantitative risk/return workbench with Monte Carlo projections and ML signals |
| **Market Positioning** | `/positioning` | Institutional, insider, and valuation data from free-tier APIs |
| **Live Microstructure** | `/live` | Real-time trades feed, Level 2 order book, and a full options Greeks terminal |
| **Momentum Alpha** | `/momentum` | Cross-sectional momentum leaderboard, single-ticker trend backtest, and screener |
| **AI Strategy Report** | `/ai-summary` | LLM-generated analyst note synthesising every metric into a written briefing |

Price history comes from `yfinance` and is cached in SQLite. Positioning data
comes from free-tier APIs (Finnhub, FMP, SEC EDGAR). Every data source degrades
gracefully — the dashboard always renders, even when a provider is slow,
rate-limited, or unconfigured.

- **Live demo:** https://ticker-change.fly.dev

---

## Features

### Price Table (`/stock`)
- Current price compared across **18 periods** — 1D–5D, 1W–3W, 1M/2M/3M/6M,
  1Y–5Y, and YTD — in both **percentage change** and **net dollar change**.
- **Custom look-back calculator**: change since *N* trading days (weekdays) or
  *N* calendar days ago, with the result shown alongside the historical price,
  net change, percentage change, and ATR-adjusted move.
- **Volatility ranges** — ATR (30-day) in dollars and as a percentage, plus
  52-week and all-time high/low.
- **Fundamentals mini-panel** — trailing/forward P/E, EV/EBITDA, P/B, EPS (TTM),
  and short % of float at a glance.
- Interactive **candlestick + volume** chart with a range selector (1D → All),
  panning support, and a dynamic TradingView deep-link.
- External links to Finviz, MarketChameleon, and ApeWisdom.

### Risk Analytics (`/analytics`)
Quantitative risk and return analysis computed from cached price history:

- **Risk stat cards** — annualised volatility, max drawdown, skewness, excess
  kurtosis, beta vs SPY, 1-day Value-at-Risk (95% and 99%), Expected Shortfall,
  mean daily return, daily standard deviation.
- **Return distribution** histogram for skew / fat-tail context.
- **Rolling 30-day annualised volatility** and **rolling Sharpe ratio**
  (1-year window).
- **Drawdown** from rolling peak.
- **Value-at-Risk** time series (95% / 99%).
- **Beta vs SPY** regression scatter.
- **Seasonality** — average return by calendar month.
- **Volume profile** — volume distribution by price level.
- **Options volatility smile** — implied vol across strikes.
- **Dealer Gamma Exposure (GEX)** — per-strike dealer gamma from an in-house
  Black-Scholes engine, surfacing the gamma-flip strike, call wall (resistance),
  put wall (support), and a positive/negative GEX regime badge.
- **Cumulative return vs benchmarks** — growth of $100 against SPY and QQQ.
- **Analyst price target** gauge (low / mean / median / high vs current price).
- **Monte Carlo forward projection** — 1,000 GBM paths at 3-month, 6-month, and
  1-year horizons with percentile cones and probability-of-up-move.
- **Consensus forecasts & guidance** — EPS and revenue estimates, upcoming
  earnings dates.
- **ML signal** — a Buy / Hold / Sell classification from a gradient-boosted
  tree model trained on OHLCV features via the triple-barrier method, with a
  plain-English feature-importance explainer.
- **AI Strategy Builder** — an LLM-generated comprehensive strategy report that
  synthesises all of the above into a professional desk-note format.

### Market Positioning (`/positioning`)
"Smart money" and sentiment context from free-tier providers:

- **Valuation & fundamentals** (Finnhub).
- **Analyst recommendation** breakdown (strong buy → strong sell).
- **Insider activity** — Finnhub insider sentiment (MSPR) and transactions,
  with SEC EDGAR Form 3/4/5 filings as a keyless fallback.
- **Institutional 13F holders** — top holders from FMP, with SEC EDGAR 13F
  filings as a fallback.

Each panel renders its own "add this key" prompt when a provider is
unconfigured, so the page is useful out of the box (SEC EDGAR needs no key) and
gets richer as you add keys.

### Live Microstructure & Options (`/live`)
A real-time terminal split across three tabs:

**Tab 1 — Market Microstructure**
- **Streaming trades feed** from the Finnhub WebSocket (when a key is set); with
  no key (or on disconnect) it falls back to a **local simulation** so the page
  still animates. A status badge shows `Live WebSocket` or `Simulated`.
- **Level 2 order book** with cumulative depth bars and a bid/ask **imbalance**
  indicator. *(L2 depth is modelled around the live spot — the free Finnhub feed
  provides trades, not full book depth.)*
- **Live VWAP**, **spread**, short-window **volatility**, **trade velocity**
  (trades/sec over 60s), **trade flow imbalance** (buy vs sell pressure), and
  **VWAP distance**.
- **Liquidity analytics** panel and a **market depth** chart.

**Tab 2 — Option Greeks & Payoff Simulator**
- **Full-width option chain** with Black-Scholes Greeks (delta, gamma, theta,
  vega, rho) computed in-house, plus volume, open interest, IV, bid/ask, and
  daily change for calls and puts.
- Adjustable **risk-free rate** and **expiration selector**.
- **Greeks visualizer** with six chart modes: Delta, Gamma, IV Smile, GEX,
  Vol/OI, and P/L %.
- **Options payoff simulator** — long/short calls & puts with break-even,
  max-profit, and max-loss stats and an interactive payoff diagram.

**Tab 3 — Quantitative Options Posture**
- **Volatility & Skew Profile** — HV30, HV90, ATM IV, IV Rank, IV Percentile.
- **Expected Move & Key Levels** — Black-Scholes expected move, straddle-based
  expected move, max pain, and put-call ratios (volume and OI).
- **Open Interest & Flow Sentiment** — call/put OI distribution and GEX
  key-level stats (call wall, put wall, zero-gamma flip, HVL).
- **Strategy recommendation engine** — suggests premium-selling or buying
  strategies based on IV Rank and IV-vs-HV comparisons, with rationale.
- **AI-Generated Option Chain Report** — an LLM synthesis of the volatility
  surface, dealer exposures, and strategic setups.

### Momentum Alpha (`/momentum`)
A quantitative relative-strength and trend-following analysis panel with three
sub-views:

- **Universe Dashboard** — cross-sectional momentum leaderboard ranking all
  cached tickers by 12-1 momentum (252-day minus 21-day return), with a
  backtest of a monthly-rebalanced top-5 portfolio vs SPY and QQQ benchmarks,
  including strategy vs hold statistics (annual return, Sharpe, max drawdown,
  Calmar, win rate, profit factor, % time invested, trade count) and a
  backtest equity curve chart. Selectable time periods (3m / 6m / 1y / 3y / all).
- **Single Ticker Scanner** — deep-dive into one ticker's momentum profile:
  multi-horizon returns (1M / 3M / 6M / 12-1), risk-adjusted momentum score,
  universe rank, trend-following backtest with trade execution stats and a
  historical momentum-score curve, plus alpha/beta/info-ratio vs SPY.
- **Cross-Sectional Momentum Screener** — filter the cached universe by minimum
  momentum thresholds to find names meeting relative-strength criteria.

### AI Strategy Report (`/ai-summary`)
- Aggregates every computed metric — risk stats, forward Monte Carlo estimates,
  valuation multiples, analyst recommendations, insider sentiment, institutional
  holders, momentum scores, ML signal, and dealer GEX positioning — into a
  single structured payload.
- Feeds the payload to an LLM with a seasoned sell-side analyst system prompt
  that grounds every claim in the data, demands bull/bear balance, and produces
  a concise desk note (~400–600 words) with sections: Snapshot, Valuation &
  Fundamentals, Technical & Volatility Picture, Options & Dealer Positioning,
  Key Risks & Catalysts, and Bottom Line.
- Raw data tables are rendered alongside the report so you can verify each claim.

### Glossary (`/glossary`)
- A single source of truth (`glossary.py`) powers inline `(i)` tooltips on every
  metric across the app and a full reference page with formulas, plain-English
  explanations, and interpretation notes.

### Settings (`/settings`)
- Configure API keys for Finnhub, FMP, and AI providers via a web UI (stored
  server-side in SQLite). Keys set here override environment variables, so you
  can add or rotate providers without a redeploy.

### UI
- Modern Tailwind design with a **dark mode** toggle (preference persisted).
- Unified ticker sub-navbar that carries the active symbol across all views.
- Cross-links between every page for the same ticker.
- All charts are interactive Plotly (zoom, pan, hover).
- Metric tooltips throughout, backed by the glossary.

---

## How it works

```
            ┌─────────────┐     prices      ┌──────────────┐
  Browser ──►   Flask     ├──► yfinance ───►│  SQLite      │
            │  routes +   │                 │  daily_prices│
            │  Plotly     ├──► Finnhub ────►│  api_cache    │
            │  ML + AI    │    FMP / SEC    │  settings     │
            └─────────────┘                 └──────────────┘
```

- **Two-tier caching.** Price history is cached in `daily_prices` (refreshed
  when older than 1h); provider and AI responses are cached in `api_cache`
  (24h / 12h TTLs respectively) to respect tight free tiers. A short in-process
  memo also collapses duplicate look-ups within a single request.
- **S3 option-chain cache.** When `S3_CACHE_BUCKET` is set, raw option chains
  for **every expiration** (plus the expiration list and spot price) are written
  to and read from S3 (4h TTL, one JSON object per ticker/expiration), so the
  cache survives redeploys and is shared across instances; a background warmer
  refreshes all chains on startup and every 4 hours. Without a bucket the same
  layer falls back to the local SQLite `api_cache`. S3-compatible stores
  (Cloudflare R2, MinIO) work via `S3_CACHE_ENDPOINT_URL` — see `.env.example`.
- **Resilient fetching.** yfinance calls retry with exponential backoff;
  provider calls go through a shared session that auto-retries on 429/5xx. If a
  refresh fails but older cached rows exist, the app serves the stale data
  instead of an error ("stale-while-error").
- **Concurrency.** The positioning page fans out its independent provider calls
  in parallel, so a cold load is bounded by the slowest call rather than the sum.
- **ML pipeline.** A gradient-boosted tree model (LightGBM if available, else
  sklearn HistGradientBoosting) is trained offline on OHLCV features using the
  triple-barrier labeling method. Labels are generated from volatility-scaled
  profit-take, stop-loss, and time barriers. Validation is walk-forward with an
  embargo to prevent label leakage. Predictions return a confidence-weighted
  Buy/Hold/Sell signal with feature-importance explanations.

---

## Quick start (local)

```bash
# 1. Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Run (development)
python3 app.py
# open http://127.0.0.1:5001
```

Production-like run with Gunicorn:

```bash
gunicorn app:app --bind 0.0.0.0:5001 --workers 2
```

The app works immediately with **no configuration** — price data and the SEC EDGAR
positioning panels need no API keys. Add keys to unlock Finnhub/FMP panels, the
live WebSocket feed, and AI analyst reports.

### Training the ML model (optional)

```bash
python ml.py train AAPL MSFT NVDA SPY ...
```

Without a trained model, the ML signal section is omitted. A stale model
guard suppresses predictions older than 30 days.

---

## Configuration

All keys are optional and degrade gracefully. Keys can be set via environment
variables, a local `.env` file, or the `/settings` page (server-side, overrides
environment).

### Market data providers

| Env var | Provider | Free tier | Powers |
| --- | --- | --- | --- |
| _(none)_ | [SEC EDGAR](https://www.sec.gov/edgar) | Unlimited | Form 3/4/5 insider filings, 13F filings — **works out of the box** |
| `FINNHUB_API_KEY` | [Finnhub](https://finnhub.io) | 60 req/min | Valuation, insider sentiment/transactions, analyst recommendations, price targets, live trades WebSocket |
| `FMP_API_KEY` | [Financial Modeling Prep](https://financialmodelingprep.com) | 250 req/day | Top institutional (13F) holders |
| `SEC_USER_AGENT` | — | — | Contact string SEC requires, e.g. `"Your Name you@email.com"` |

### AI analyst report providers (any one is enough; tried in order)

| Env var | Provider | Default model |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | Anthropic (Claude) | `claude-opus-4-8` |
| `OPENAI_API_KEY` | OpenAI | `gpt-4o` |
| `GEMINI_API_KEY` | Google Gemini | `gemini-3.1-flash-lite` |
| `OPENROUTER_API_KEY` | OpenRouter | `anthropic/claude-opus-4-8` |

Providers are tried in order — if one is missing, errored, or out of quota, it
falls back to the next. With no AI provider configured, the report section
simply doesn't render.

### Example `.env`

```bash
# Market data
FINNHUB_API_KEY=your_key
FMP_API_KEY=your_key
SEC_USER_AGENT=Your Name you@email.com

# AI analyst (any one is enough)
ANTHROPIC_API_KEY=your_key
# OPENAI_API_KEY=your_key
# GEMINI_API_KEY=your_key
# OPENROUTER_API_KEY=your_key
```

---

## API endpoints

JSON variants of every view, plus health and config:

| Endpoint | Returns |
| --- | --- |
| `GET /api/stock/<ticker>` | Current price, per-period % and net change, chart metadata |
| `GET /api/chart-data/<ticker>` | OHLCV history (supports `?start_date=&end_date=`) |
| `GET /api/analytics/<ticker>` | Risk stats and metrics (chart HTML stripped) |
| `GET /api/positioning/<ticker>` | Valuation, recommendations, insider, institutional data |
| `GET /api/options-greeks/<ticker>` | Option chain with Black-Scholes Greeks (supports `?expiration=&rf_rate=`) |
| `GET /api/options-analysis/<ticker>` | Quantitative options posture: IV rank, expected move, max pain, PCR, GEX, strategy recommendation |
| `GET /api/options-ai-report/<ticker>` | AI-generated option chain report (HTML) |
| `GET /api/config` | Client config for the live page (Finnhub key availability) |
| `GET /api/raw-sec-filings/<ticker>` | Recent SEC EDGAR filings (all form types) |
| `GET /health` | `{"status": "ok"}` for uptime checks |

---

## Deploy notes (Fly.io & Render)

- **Live Fly.io App:** https://ticker-change.fly.dev
- **Fly.io Setup:** App is configured via `fly.toml` with Docker container deployment and a 1GB persistent volume (`stocks_data`) mounted at `/data`.
- **Database Persistence:** `DB_PATH` is set to `/data/stocks.db` on Fly.io, preserving user API keys, cached options chains, and historical prices across restarts.
- **Deployment Command:** Deploy to Fly.io using `fly deploy --ha=false` (single-instance deploy avoids SQLite desync).
- Repo also includes a `Dockerfile`, `Procfile`, and `render.yaml` suitable for Render deployment.

---

## Project structure

```
app.py            Flask app: routes, analytics, chart generation, options pricing,
                  momentum backtest, fetch orchestration (3,200+ lines)
providers.py      Free-tier API clients (Finnhub, FMP, SEC EDGAR) with caching,
                  retries, and multi-key rotation; AI provider resolution
db.py             SQLite layer: price cache, provider cache, settings store
ml.py             ML pipeline: triple-barrier labels, feature engineering,
                  walk-forward validation, gradient-boosted tree model
ai.py             LLM analyst reports: standard, comprehensive, and options-
                  specific report generators with multi-provider fallback
glossary.py       Single source of truth for metric definitions (tooltips + /glossary)
templates/        Jinja2 templates (base, index, stock, analytics, positioning,
                  live, momentum, ai_summary, settings, glossary)
requirements.txt  Python dependencies
Dockerfile        Container build for deployment
fly.toml          Fly.io service & volume mount configuration
Procfile          Gunicorn process definition for Render/Heroku
render.yaml       Render service definition with env-var declarations
.env.example      Example environment configuration
```

---

## Tech stack

- **Backend:** Flask, yfinance, pandas, numpy
- **Charts:** Plotly (interactive candlesticks, distributions, scatter, 3D surfaces)
- **ML:** LightGBM / scikit-learn (gradient-boosted trees, triple-barrier labeling)
- **AI:** Anthropic, OpenAI, Google Gemini, OpenRouter (multi-provider fallback)
- **Data:** Finnhub, Financial Modeling Prep, SEC EDGAR, yfinance
- **Frontend:** Tailwind CSS, Jinja2, Plotly.js
- **Deployment:** Fly.io (with Persistent Volume), Gunicorn, Docker, Render

---

## Notes

- Built on `yfinance`, `pandas`/`numpy`, `plotly`, and `Flask`.
- Do not commit secrets — keep keys in environment variables or via the
  `/settings` page. `render.yaml` uses `sync: false` for all sensitive vars.
- The ML signal and AI reports are educational tools on delayed end-of-day data —
  not financial advice.
