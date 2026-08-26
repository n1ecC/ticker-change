# ticker/change

**A personal equity research terminal for solo and swing traders.**

Enter a ticker, understand the context, sanity-check a trade before you take it.
ticker/change pulls free market data into six connected views — prices,
fundamentals, options positioning, momentum, and risk — so you can decide
whether a name is worth your attention. It explains what the data shows; it
does not predict the market or execute trades.

- **Live demo:** https://ticker-change.fly.dev

---

## What it is

| | |
| --- | --- |
| **Category** | Personal equity research terminal / pre-trade context cockpit |
| **Built for** | Solo traders and swing traders researching their own ideas |
| **Data** | Free-tier sources (`yfinance`, Finnhub, FMP, SEC EDGAR) |
| **Architecture** | Single-process Flask app, SQLite cache, no frontend build step |

**Not:** a broker, a validated alpha engine, financial advice, or a replacement
for Bloomberg. Buyer Signals and ML outputs are structured summaries — not
forecasts. See [What we don't claim](#what-we-dont-claim).

---

## How you'll use it

Two modes, one app:

| When | Question | Flow |
| --- | --- | --- |
| **Morning** | What deserves attention today? | Scan → pick 1–3 names |
| **Pre-trade** | Is this swing worth taking? | Deep dive → go or pass |

```
/morning watchlist  →  Morning flags  →  Six views per ticker  →  Checklist on /analytics
```

**Workflow:** add symbols on [`/morning`](https://ticker-change.fly.dev/morning) → scan flags → open **Check** → pre-trade checklist on `/analytics`.

See [`docs/PRODUCT_STRATEGY.md`](docs/PRODUCT_STRATEGY.md) for roadmap (trade journal deferred).

---

## Three layers

Everything maps to one of three layers:

```
┌─────────────────────────────────────────┐
│  DECIDE  — watchlist, morning scan, pre-trade checklist │  built
├─────────────────────────────────────────┤
│  UNDERSTAND — GEX, IV, momentum, risk   │  built ← current strength
├─────────────────────────────────────────┤
│  OBSERVE — prices, fundamentals, flow   │  built
└─────────────────────────────────────────┘
```

---

## Views

Enter a ticker and navigate six connected views. The sub-navbar carries your
symbol across every page.

| View | Route | Purpose |
| --- | --- | --- |
| **Morning scan** | `/morning` | Watchlist + alert flags (earnings, IV, momentum, insider, extension) |
| **Price Table** | `/stock` | Multi-period change, candlestick chart, fundamentals glance |
| **Risk Analytics** | `/analytics` | Volatility, VaR, GEX, Monte Carlo, **pre-trade checklist**, Buyer Signals |
| **Market Positioning** | `/positioning` | Valuation, analyst ratings, insider activity, 13F holders |
| **Live & Options** | `/live` | Trades feed, option chain + Greeks, IV rank, strategy posture |
| **Momentum** | `/momentum` | Cross-sectional rank, screener, single-ticker backtest |
| **AI Report** | `/ai-summary` | LLM desk note synthesising metrics (optional; needs AI key) |

Also: [`/glossary`](https://ticker-change.fly.dev/glossary) (metric reference),
[`/settings`](https://ticker-change.fly.dev/settings) (API keys),
[`/api/docs`](https://ticker-change.fly.dev/api/docs) (OpenAPI explorer).

---

## Features by layer

### Observe — aggregate public data

**`/stock`**
- 18-period price change (1D–5D through 5Y + YTD), percentage and net dollar
- Custom lookback: *N* trading days or calendar days ago
- ATR (30d), 52-week and all-time ranges
- Fundamentals mini-panel (P/E, EV/EBITDA, short % float, …)
- Interactive candlestick + volume chart (Plotly)

**`/positioning`**
- Valuation and fundamentals (Finnhub)
- Analyst recommendation breakdown
- Insider sentiment and transactions (Finnhub + SEC EDGAR fallback)
- Top institutional 13F holders (FMP + SEC fallback)

SEC EDGAR panels work with **no API keys**.

### Understand — compute context

**`/analytics`**
- Risk stats: vol, max drawdown, beta, VaR (95/99), Sharpe, skew, kurtosis
- Rolling vol, drawdown, and VaR time series
- **Dealer Gamma Exposure (GEX)** — modelled from OI + in-house Black-Scholes
- Options vol smile, cumulative return vs SPY/QQQ
- Monte Carlo forward paths (3m / 6m / 1y)
- **Buyer Signals** — transparent multi-factor tally (`signals.py`), not a black box
- ML Buy/Hold/Sell signal (optional; train with `ml.py`)

**`/live`**
- Streaming trades (Finnhub WebSocket when keyed; simulated fallback)
- Full option chain with Greeks, IV smile, GEX charts, payoff simulator
- IV rank / percentile, expected move, max pain, put-call ratios
- Strategy posture suggestions based on IV vs HV

**`/momentum`**
- Universe momentum leaderboard (12-1 month rank)
- Cross-sectional screener with minimum momentum filters
- Single-ticker trend backtest with alpha/beta vs SPY

**`/ai-summary`**
- LLM analyst note grounded in computed metrics (multi-provider fallback)
- Raw data tables rendered alongside for verification

Every metric has an `(i)` tooltip linking to [`/glossary`](/glossary).

### Decide — go/no-go support

**`/morning`**
- SQLite watchlist (up to 25 symbols) with trade type and note
- Compact scan table: 1D/5D change, momentum rank, alert flags
- Flags: earnings ≤5d, IV rank >70, momentum extremes, insider net sell, >2 ATR extension

**`/analytics` (checklist card)**
- Six pass / warn / fail checks: trend, momentum, IV, earnings, GEX, extension
- One-line verdict summary; linked from morning **Check** action

**API:** `GET /api/morning`, `GET /api/checklist/<ticker>`

Trade journal — deferred. Details: [`docs/PRODUCT_STRATEGY.md`](docs/PRODUCT_STRATEGY.md)

---

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python3 app.py
# → http://127.0.0.1:5001
```

Production-like:

```bash
gunicorn app:app --bind 0.0.0.0:5001 --workers 2
```

Works immediately with **no configuration** — price data and SEC EDGAR panels
need no keys. Add keys via [`.env.example`](.env.example) or `/settings` to
unlock Finnhub/FMP panels, live WebSocket, and AI reports.

**Optional — train the ML model:**

```bash
python ml.py train AAPL MSFT NVDA SPY ...
```

Without `model.pkl`, the ML section is omitted. Predictions older than 30 days
are suppressed.

---

## Configuration

All keys optional; every layer degrades gracefully. Settings-page values
override environment variables.

### Market data

| Env var | Provider | Powers |
| --- | --- | --- |
| _(none)_ | [SEC EDGAR](https://www.sec.gov/edgar) | Insider + 13F filings — **no key needed** |
| `FINNHUB_API_KEY` | [Finnhub](https://finnhub.io) | Fundamentals, insider, analyst data, live trades |
| `FMP_API_KEY` | [FMP](https://financialmodelingprep.com) | Institutional 13F holders |
| `SEC_USER_AGENT` | — | Required contact string for SEC, e.g. `"Name you@email.com"` |

### AI reports *(any one provider is enough)*

| Env var | Provider |
| --- | --- |
| `ANTHROPIC_API_KEY` | Anthropic (Claude) |
| `OPENAI_API_KEY` | OpenAI |
| `GEMINI_API_KEY` | Google Gemini |
| `OPENROUTER_API_KEY` | OpenRouter |

Providers are tried in order; with none configured, AI sections simply don't render.

---

## API

JSON twins of every view, plus health and config:

| Endpoint | Returns |
| --- | --- |
| `GET /api/morning` | Watchlist scan with alert flags |
| `GET /api/checklist/<ticker>` | Pre-trade checklist (`?trade_type=long_stock`) |
| `GET /api/stock/<ticker>` | Price, period changes, chart metadata |
| `GET /api/analytics/<ticker>` | Risk stats and metrics |
| `GET /api/positioning/<ticker>` | Valuation, insider, institutional data |
| `GET /api/options-greeks/<ticker>` | Option chain + Greeks |
| `GET /api/options-analysis/<ticker>` | IV rank, expected move, GEX, strategy posture |
| `GET /api/chart-data/<ticker>` | OHLCV history |
| `GET /health` | `{"status": "ok"}` |

Full schema: [`/api/docs`](/api/docs)

---

## How it works

```
  Browser ──►  Flask (routes, Plotly, ML, AI)
                  │
                  ├── yfinance ──► SQLite (daily_prices, api_cache, settings)
                  ├── Finnhub / FMP / SEC EDGAR
                  └── S3 (optional option-chain cache)
```

- **Caching:** 1h price freshness, 24h provider cache, 45s in-process memo
- **Option chains:** S3 first (4h TTL, survives redeploys), then SQLite, then live fetch
- **Resilience:** stale-while-error — serves cached data when refresh fails
- **ML:** triple-barrier labels, walk-forward validation, LightGBM/sklearn

More detail: [`CLAUDE.md`](CLAUDE.md)

---

## Deploy

**Live:** https://ticker-change.fly.dev

```bash
fly deploy --ha=false   # single instance — SQLite has no multi-writer story
```

Fly.io mounts a persistent volume at `/data` for `stocks.db`. Also ships with
`Dockerfile`, `Procfile`, and `render.yaml` for Render.

---

## Project structure

```
decide.py         Morning scan flags and pre-trade checklist
app.py          Routes, analytics, charts, options pricing, momentum backtest
providers.py    Finnhub / FMP / SEC / AI clients (fail gracefully)
db.py           SQLite: prices, api_cache, settings
signals.py      Buyer Signals factor tally
ml.py           Offline ML training and inference
ai.py           LLM analyst reports
glossary.py     Metric definitions (tooltips + /glossary)
templates/      Jinja2 + Tailwind CDN + Plotly
```

---

## What we don't claim

- Market-beating alpha or validated edge (unless backtested and published in-app)
- Real-time institutional order flow or measured dealer positioning
- Financial advice or trade recommendations
- Replacement for a broker or professional risk system

GEX is modelled from open interest, not observed flow. Buyer Signals is a
descriptive heuristic tally. ML and AI outputs are educational tools on
delayed data.

---

## Docs

| Doc | Contents |
| --- | --- |
| [`docs/PRODUCT_IDENTITY.md`](docs/PRODUCT_IDENTITY.md) | Category, positioning, principles, vocabulary |
| [`docs/PRODUCT_STRATEGY.md`](docs/PRODUCT_STRATEGY.md) | Personas, watchlist roadmap, first sprint |
| [`docs/ROADMAP.md`](docs/ROADMAP.md) | DuckDB / dbt analytics pipeline (separate track) |
| [`CLAUDE.md`](CLAUDE.md) | Architecture guide for contributors |

---

## Tech stack

Flask · yfinance · pandas · numpy · Plotly · LightGBM/sklearn · Tailwind (CDN) ·
Jinja2 · Gunicorn · Fly.io

Do not commit secrets — use environment variables or `/settings`.
