# ticker/change

A Flask stock-analytics dashboard. Enter a ticker and get four connected views:
a multi-period price-change table, a quantitative risk/statistics workbench, a
market-positioning panel built from institutional, insider, and valuation data, and
a live market-microstructure + options-Greeks terminal.

Price history comes from `yfinance` and is cached in SQLite; positioning data comes
from free-tier APIs (Finnhub, FMP, SEC EDGAR) and is cached separately. Every data
source degrades gracefully, so the dashboard always renders even when a provider is
slow, rate-limited, or unconfigured.

- **Live demo:** https://ticker-change.onrender.com

---

## Features

### 📊 Price change table (`/stock`)
- Current price compared across **18 periods** — 1D–5D, 1W–3W, 1M/2M/3M/6M, 1Y–5Y, and YTD.
- Both **% change** and **net dollar change** for every period.
- **Custom look-back calculator**: change since *N* trading days (weekdays) or *N* calendar days ago.
- **ATR (30-day)** in dollars and as a percentage, plus **52-week** and **all-time** high/low ranges.
- Interactive **candlestick + volume** chart with a range selector (1D → All).

### 📈 Analytics workbench (`/analytics`)
Quantitative risk and return analysis computed from the cached price history:

- **Risk stat cards** — annualised volatility, max drawdown, skewness, excess kurtosis,
  beta vs SPY, 1-day Value-at-Risk (95% and 99%), mean daily return, daily std dev.
- **Return distribution** histogram (skew / fat-tail context).
- **Rolling volatility** (30-day, annualised) and **rolling Sharpe ratio** (1-year window).
- **Drawdown** from rolling peak.
- **Value-at-Risk** time series (95% / 99%).
- **Beta vs SPY** regression scatter.
- **Seasonality** — average return by calendar month.
- **Volume profile** — volume distribution by price level.
- **Options volatility smile** — implied vol across strikes.
- **Dealer Gamma Exposure (GEX)** — per-strike dealer gamma from an in-house Black-Scholes
  engine, surfacing the gamma-flip strike, call wall (resistance), and put wall (support).
- **Cumulative return vs benchmarks** — growth of $100 against SPY and QQQ.
- **Analyst price target** gauge (low / mean / median / high vs current price).
- **Fundamentals panel** — P/E (trailing & forward), EV/EBITDA, P/B, EPS, short % of float,
  short ratio, dividend yield, sector, and industry.

### 🏛️ Market positioning (`/positioning`)
"Smart money" and sentiment context, assembled from free-tier providers (see
[Configuration](#configuration)):

- **Valuation & fundamentals** (Finnhub).
- **Analyst recommendation** breakdown (strong buy → strong sell).
- **Insider activity** — Finnhub insider sentiment (MSPR) and transactions, with SEC EDGAR
  Form 3/4/5 filings as a keyless fallback.
- **Institutional 13F holders** — top holders from FMP, with SEC EDGAR 13F filings as a fallback.

Each panel renders its own "add this key" prompt when a provider is unconfigured, so the
page is useful out of the box (SEC EDGAR needs no key) and gets richer as you add keys.

### ⚡ Live microstructure & Greeks (`/live`)
A real-time terminal split across two tabs:

- **Market microstructure** — a streaming **trades feed**, an animated **Level 2 order book**
  with cumulative depth and a bid/ask **imbalance** bar, plus live **VWAP**, **spread**, and
  short-window **volatility**. Trades stream from the **Finnhub WebSocket** when a
  `FINNHUB_API_KEY` is set; with no key (or on disconnect) the page automatically falls back to
  a **local simulation** so it still animates out of the box. A status badge shows whether the
  feed is `Live WebSocket` or `Simulated`. *(L2 depth is modelled around the live spot — the
  free Finnhub feed provides trades, not full book depth.)*
- **Option Greeks & payoff simulator** — an **option chain** with Black-Scholes Greeks
  (delta, gamma, theta, vega, rho) computed in-house, a **Greeks visualizer** (delta / gamma /
  IV smile / GEX), an adjustable risk-free rate and expiration selector, and an **options
  payoff simulator** (long/short calls & puts) with break-even, max-profit, and max-loss stats.

### 🎨 UI
- Modern Tailwind design with a **dark mode** toggle (preference persisted).
- Cross-links between the price, analytics, positioning, and live views for the same ticker.
- All charts are interactive Plotly (zoom, pan, hover).

---

## How it works

```
            ┌─────────────┐     prices      ┌──────────────┐
  Browser ──►   Flask     ├──► yfinance ───►│  SQLite      │
            │  routes +   │                 │  daily_prices│
            │  Plotly     ├──► Finnhub ────►│  api_cache   │
            └─────────────┘    FMP / SEC    └──────────────┘
```

- **Two-tier caching.** Price history is cached in `daily_prices` (refreshed when older
  than 1h); provider responses are cached in `api_cache` (24h TTL) to respect tight free
  tiers. A short in-process memo also collapses duplicate look-ups within a single request.
- **Resilient fetching.** yfinance calls retry with exponential backoff; provider calls go
  through a shared session that auto-retries on 429/5xx. If a refresh fails but older cached
  rows exist, the app serves the stale data instead of an error ("stale-while-error").
- **Concurrency.** The positioning page fans out its independent provider calls in parallel,
  so a cold load is bounded by the slowest call rather than the sum of all of them.

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
positioning panels need no API keys. Add keys to unlock the Finnhub/FMP panels.

---

## Configuration

Positioning data is powered by free-tier providers, gated behind environment variables
(read from the shell or a local `.env` file). Each is optional and degrades gracefully.

| Env var | Provider | Free tier | Powers |
| --- | --- | --- | --- |
| _(none)_ | [SEC EDGAR](https://www.sec.gov/edgar) | Unlimited | Form 3/4/5 insider filings, 13F filings — **works out of the box** |
| `FINNHUB_API_KEY` | [Finnhub](https://finnhub.io) | 60 req/min | Valuation metrics, insider sentiment (MSPR), insider transactions, analyst recommendations, price targets |
| `FMP_API_KEY` | [Financial Modeling Prep](https://financialmodelingprep.com) | 250 req/day | Top institutional (13F) holders |
| `SEC_USER_AGENT` | — | — | Contact string SEC requires, e.g. `"Your Name you@email.com"` |

Example `.env`:

```bash
FINNHUB_API_KEY=your_key
FMP_API_KEY=your_key
SEC_USER_AGENT=Your Name you@email.com
```

---

## API endpoints

JSON variants of every view, plus a health check:

| Endpoint | Returns |
| --- | --- |
| `GET /api/stock/<ticker>` | Current price, per-period % and net change, chart metadata |
| `GET /api/chart-data/<ticker>` | OHLCV history (supports `?start_date=&end_date=`) |
| `GET /api/analytics/<ticker>` | Risk stats and metrics (chart HTML stripped) |
| `GET /api/positioning/<ticker>` | Valuation, recommendations, insider, institutional data |
| `GET /api/options-greeks/<ticker>` | Option chain with Black-Scholes Greeks (supports `?expiration=&rf_rate=`) |
| `GET /api/config` | Client config for the live page (Finnhub key availability) |
| `GET /health` | `{"status": "ok"}` for uptime checks |

---

## Deploy notes (Render)

- Repo includes a `Dockerfile` and `Procfile` suitable for Render.
- Recommended health-check path: `/health`.
- Enable `Auto-Deploy` for the connected branch, or trigger a manual deploy.
- Set API keys as environment variables in the Render dashboard.
- Note: the SQLite cache lives on the app's local disk, which is ephemeral on Render — it
  acts purely as a cache and is rebuilt from the providers after a redeploy.

---

## Project structure

```
app.py            Flask app: routes, analytics, chart generation, fetch orchestration
providers.py      Free-tier API clients (Finnhub, FMP, SEC EDGAR) with caching + retries
db.py             SQLite layer: price cache, provider cache, freshness helpers
templates/        Jinja2 templates (base, index, stock, analytics, positioning, live)
requirements.txt  Python dependencies
Dockerfile        Container build for deployment
Procfile          Gunicorn process definition for Render/Heroku
```

---

## Notes

- Built on `yfinance`, `pandas`/`numpy`, `plotly`, and `Flask`.
- Do not commit secrets — keep keys in environment variables or an untracked `.env`.
