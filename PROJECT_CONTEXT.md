# Project Context: Macro-Financial & Market Intelligence Engine (`ticker-change`)

## 1. Executive Summary

`ticker-change` is today a production Flask stock-analytics dashboard (live at
`ticker-change.onrender.com`) that turns a single ticker into four connected
views: a multi-period **price-change table**, a quantitative **risk/statistics
workbench**, a **market-positioning panel**, and a **live microstructure +
options-Greeks terminal**. It already computes annualised volatility, max
drawdown, skew/kurtosis, beta vs SPY, Value-at-Risk, dealer gamma exposure
(GEX), and a deterministic multi-factor buyer-signal composite — all from a
resilient ingestion layer (yfinance + Finnhub + FMP + SEC EDGAR) backed by a
two-tier SQLite cache.

The **Macro-Financial & Market Intelligence Engine** is the platform's next
evolution: an **Analytical Data Warehouse (OLAP) tier** that lifts these
per-ticker, point-in-time analytics out of the operational cache and onto a
columnar **DuckDB** warehouse modeled with **dbt**, then enriches them with
**macro-financial telemetry** (FRED CPI, Effective Federal Funds Rate, yield
curve spreads) and **SEC EDGAR filing sentiment**. The result is the ability to
ask *macro-conditional* questions of data the app already produces: how do
volatility regimes, dealer-gamma flips, and buyer-signal conviction diverge
across monetary-policy and inflation regimes, and do corporate-action events
(ticker migrations, 8-K material events, 10-K/10-Q language shifts) cluster
around specific macro states?

The extension is **additive and non-disruptive**: the operational Flask app and
its `stocks.db` cache keep running unchanged. A decoupled ELT pipeline reads
from the operational store (DuckDB attaches SQLite natively), stages external
feeds as Parquet, and publishes Kimball-style dimensional marts consumed by an
Evidence.dev BI layer.

---

## 2. System Baseline (What Exists Today)

The extension is built on top of these existing, verified components:

### 2.1 Operational Data Store — `stocks.db`
| Table | Schema | Purpose |
| --- | --- | --- |
| `tickers` | `symbol TEXT PK, last_fetched DATETIME` | Freshness registry for price history (1h TTL). |
| `daily_prices` | `symbol, date, open, high, low, close, volume` (PK `symbol, date`) | OHLCV price cache sourced from yfinance. |
| `api_cache` | `provider, key, fetched_at, payload` (PK `provider, key`) | 24h-TTL JSON cache for Finnhub/FMP/SEC responses. |
| `app_settings` | `key TEXT PK, value TEXT` | User-supplied API keys saved via `/settings`. |

### 2.2 Provider Layer (`providers.py`)
- **Shared `requests.Session`** with `urllib3.Retry(total=3, backoff_factor=0.6)`,
  retrying `429/500/502/503/504`, honouring `Retry-After`, 12s timeout.
- **Key rotation**: env var → user keys from `app_settings` → built-in shared dev
  key. Rollover triggered on `401/402/403/429`.
- **Providers**: yfinance (prices, keyless), Finnhub (60 req/min, valuation /
  insider / analyst), FMP (250 req/day, 13F holders), SEC EDGAR (keyless, needs
  `SEC_USER_AGENT`; Form 3/4/5 + 13F filings).
- **Degradation**: every call returns `None` on failure; the dashboard always
  renders ("stale-while-error" serves older cached rows when a refresh fails).

### 2.3 Application Surface (`app.py`)
Routes: `/`, `/health`, `/glossary`, `/stock`, `/analytics`, `/positioning`,
`/live`, `/momentum`, `/settings`, `/ai-summary`, plus JSON `/api/*` variants and
`/api/options-greeks/<ticker>`. Positioning page **fans out independent provider
calls in parallel** (bounded by the slowest call, not the sum).

### 2.4 Analytics Already Computed
Risk stats (annualised vol, max drawdown, skew, excess kurtosis, beta vs SPY,
1-day VaR 95%/99%), rolling vol & Sharpe, drawdown series, VaR time series,
beta regression, seasonality, volume profile, IV smile, **GEX** (gamma-flip
strike, call/put walls), cumulative return vs SPY/QQQ, analyst targets,
fundamentals (P/E, EV/EBITDA, P/B, EPS, short %, dividend yield).

### 2.5 Intelligence Layers
- **`signals.py`** — deterministic multi-factor buyer-signal model. Weighted
  factors: `trend 0.20`, `momentum 0.16`, `gex 0.18`, `monte_carlo 0.15`,
  `volatility 0.08`, `tail_risk 0.05`, `valuation 0.10`, `ml 0.08` → composite
  0–100. Fully auditable; same inputs ⇒ same outputs.
- **`ml.py` + `model.pkl`** — LightGBM/scikit-learn directional model.
- **`ai.py`** — LLM narration of the signal reads (Anthropic → OpenAI → Gemini
  → OpenRouter fallback chain).

---

## 3. Core Problem Statement & Hypotheses

Per-ticker analytics today are **macro-blind**: the dashboard can tell you a
stock's volatility or dealer-gamma exposure, but not whether either is
unusual *given the prevailing monetary-policy and inflation regime*. The
extension formalises three hypotheses:

* **H1 — Regime conditioning:** Volatility, drawdown depth, and GEX gamma-flip
  proximity behave differently across rate-hike vs. rate-cut cycles. Controlling
  for EFFR and 2s10s spread should materially shift the distribution of these
  metrics.
* **H2 — Corporate-action clustering:** SEC-reported corporate actions (ticker
  migrations, 8-K material events) and 10-K/10-Q MD&A language shifts cluster
  around specific macro states (tightening cycles, yield-curve inversion).
* **H3 — Signal edge under regime:** The buyer-signal composite's descriptive
  read weakens or inverts during high-inflation / inverted-curve regimes;
  quantifying this makes the signal more honest and more useful.

---

## 4. Strategic Objectives & North-Star Metrics

| Objective | North-Star Metric | Target |
| --- | --- | --- |
| Macro-contextualise existing analytics | % of marts joinable to a macro regime on event date | 100% by Sprint 3 |
| Make the data warehouse reproducible | End-to-end pipeline runtime (cold) | < 8 min on a laptop |
| Enforce data quality at the boundary | dbt test pass rate | ≥ 99% over 30 days |
| Keep the operational app untouched | Zero changes to `app.py` / `db.py` contract | Continuous |

---

## 5. System Architecture (Existing + Extension)

```
[ OPERATIONAL TIER — unchanged, runs in production today ]
├── Flask app (app.py)            routes: /stock /analytics /positioning /live /momentum
├── providers.py                  yfinance · Finnhub · FMP · SEC EDGAR (retry+rotation)
├── stocks.db (SQLite)            tickers · daily_prices · api_cache · app_settings
├── signals.py / ml.py / ai.py    buyer-signal composite + LLM narration
└── Render deploy (Dockerfile + Procfile, gunicorn, ephemeral cache)
        │  (read-only, via DuckDB sqlite_scanner — no writes back to operational DB)
        ▼
[ ANALYTICS STORAGE TIER — new ]
└── analytics/warehouse.duckdb
    ├── ATTACH 'stocks.db' (sqlite_scanner)        # live read of operational data
    ├── raw_fred_* (Parquet stage)                 # CPI, EFFR, T10Y2Y, DGS10, etc.
    └── raw_sec_* (Parquet stage)                  # 10-K/10-Q/8-K metadata + text
        │
        ▼
[ TRANSFORMATION TIER — new, dbt-duckdb ]
└── analytics/dbt_project/
    ├── staging    stg_daily_prices · stg_api_cache · stg_fred_macro · stg_sec_filings
    ├── intermediate int_ticker_macro_aligned (temporal range joins on effective date)
    └── marts      fct_ticker_macro_impact · fct_signal_regime · dim_companies(SCD2) · dim_macro_regime
        │
        ▼
[ PRESENTATION TIER — new ]
└── dashboards/ (Evidence.dev)   SQL-backed markdown: regime heatmaps, signal-vs-regime, GEX-vs-curve
```

The decisive architectural choice is the **read-only attachment**: DuckDB
queries `stocks.db` in place via `sqlite_scanner`, so the warehouse never
mutates operational state and the Flask app's caching semantics stay identical.

---

## 6. Data Sources & Contracts

### 6.1 Reused (Operational)
| Source | Fields used in warehouse | Cadence |
| --- | --- | --- |
| `daily_prices` | `symbol, date, close, volume` | On-demand (1h freshness) |
| `api_cache` | provider payloads (fundamentals, 13F, insider) | 24h TTL |
| `signals.py` outputs | composite score + per-factor reads | Computed on demand |

### 6.2 New External Sources
| Source | Series / Entities | API | Rate limit | Key |
| --- | --- | --- | --- | --- |
| **FRED** (St. Louis Fed) | `CPIAUCSL` (CPI), `DFF` (EFFR), `T10Y2Y` (2s10s), `DGS10` (10Y), `DGS2` (2Y) | `api.stlouisfed.org/fred/series/observations` | 120 req/min | `FRED_API_KEY` (free) |
| **SEC EDGAR** | 10-K / 10-Q / 8-K filings, `company_tickers.json`, submissions by CIK | `data.sec.gov` | 10 req/sec (polite) | None — `SEC_USER_AGENT` contact string |
| **FRED regime classifier** | NBER-dated recession windows, Fed policy cycle tags | Derived | — | — |

All external ingestion inherits the operational tier's resilience pattern:
shared session, exponential backoff, `Retry-After` honoured, idempotent writes
to Parquet (overwrite-by-partition), and a run-state log so re-runs skip
already-fetched windows.

---

## 7. Technology Stack & Rationale

| Choice | Why |
| --- | --- |
| **DuckDB** | Single-file columnar DB; reads SQLite + Parquet natively; zero server; in-process SQL that outperforms SQLite for analytic scans. |
| **dbt (duckdb adapter)** | SQL-first transformation with version control, tests, docs, and lineage — the industry standard for warehouse modeling. |
| **Parquet staging** | Columnar, compressed, partitionable by date; minimises memory and lets DuckDB push down filters. |
| **Evidence.dev** | SQL-native BI that lives in the repo alongside dbt; markdown + charts, static-site output, no server to run. |
| **Python orchestrator** | Reuses the project's existing Python toolchain; no new runtime for a 4-step nightly pipeline. |

Rejected alternatives: Snowflake/BigQuery (overkill for single-file local
warehouse), Airflow (operational overhead exceeds a 4-step DAG's needs —
Prefect or a plain Python runner suffices), Streamlit (kept as a fallback if
Evidence.dev's SQL-only model proves too rigid for the GEX visualisations).

---

## 8. Analytical Model: Metrics, KPIs & Formulas

| Metric | Definition | Inputs |
| --- | --- | --- |
| **Corporate Pivot Velocity** | 30-day rolling count of SEC corporate-action events (ticker migration + 8-K material events) per GICS sector, z-scored against its 1-year mean. | `stg_sec_filings`, `dim_companies` |
| **Regime-Conditional Volatility** | Annualised vol (existing calc) grouped by `dim_macro_regime` (hike/cut/hold/inverted-curve). | `stg_daily_prices`, `dim_macro_regime` |
| **GEX Gamma-Flip Proximity** | Distance between spot and gamma-flip strike, normalised by 30-day realised vol, averaged per regime. | `signals.py` GEX output, `dim_macro_regime` |
| **Signal Conviction Gap** | Mean buyer-signal composite in tight vs. loose regimes; H3 predicts a statistically significant divergence. | `fct_signal_regime`, Welch's t-test |
| **Filing Sentiment Trajectory** | FinBERT sentiment of MD&A (Item 7) text in the 4 quarters flanking a corporate-action event. | `stg_sec_filings`, NLP stage |
| **Sector Restructuring Index** | Ticker-change count per sector / sector size, mapped to 30-day EFFR delta. | `stg_sec_filings`, `stg_fred_macro` |

Statistical rigour: every comparative claim is reported with sample size,
effect size (Cohen's *d*), and a 95% CI; multiple-testing correction
(Benjamini–Hochberg) is applied when comparing across sectors.

---

## 9. Data Governance, Quality & Security

* **Read-only boundary.** The warehouse attaches `stocks.db` read-only; no
  dbt model writes back. Operational contract (`db.py`) is untouched.
* **PII / secrets.** No PII is ingested. API keys never enter the warehouse;
  ingestion reads them from env/`app_settings` at fetch time only. `.env` stays
  gitignored; `.env.example` documents required vars.
* **SEC EDGAR etiquette.** `SEC_USER_AGENT` set on every request; capped at
  10 req/sec with jitter; full-filing text fetched only when needed for NLP.
* **Freshness SLOs.** Macro series: ≤ 24h lag. SEC filings: ≤ 7d lag. Prices:
  inherited from operational 1h TTL.
* **Reproducibility.** Every Parquet partition carries `ingested_at` and
  `source_url`; dbt models are pure functions of their inputs (no `now()`).

---

## 10. Scalability & Performance Considerations

* **DuckDB scales to the workload.** A decade of OHLCV for ~8k tickers plus
  full FRED history is low-single-digit GB; DuckDB handles this in-process in
  seconds. No clustering needed.
* **Incremental loading.** FRED and SEC ingestion are windowed
  (`observation_start` / `filing_date >= last_run`); dbt models use
  `unique`+`not_null` tests plus incremental materialisations to avoid full
  recompute.
* **Memory.** Staging to Parquet (not in-DuckDB tables) keeps the working set
  columnar and spillable; the orchestrator never holds a full filing corpus in
  memory — sentiment is scored per-filing and written back as a row.
* **Concurrency.** Ingestion steps are I/O-bound and run in a thread pool;
  dbt runs are serial (warehouse-local). The operational Flask app is
  unaffected because warehouse queries hit the attached SQLite read-only.

---

## 11. Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
| --- | --- | --- | --- |
| SEC EDGAR rate-limiting / IP block | Med | High | 10 req/s cap + jitter + `Retry-After`; cache raw responses to Parquet |
| FinBERT model size / latency | Med | Med | Score offline in the orchestrator, persist sentiment scores, never at request time |
| Operational schema drift in `stocks.db` | Low | High | Staging models cast defensively; dbt tests fail the pipeline before marts publish |
| Macro series revisions (FRED republishes) | Med | Low | Idempotent overwrite-by-date partition; `revised_at` captured |
| Evidence.dev SQL limits for GEX charts | Med | Low | Fall back to Streamlit for the GEX-specific visualisations only |
| Over-claiming causality | Med | High | Docs/tests enforce "regime-conditional", not "causal"; every metric ships with sample size + CI |

---

## 12. Glossary

- **EFFR** — Effective Federal Funds Rate (FRED `DFF`).
- **2s10s spread** — 10Y minus 2Y Treasury yield (`T10Y2Y`); negative ⇒ inversion.
- **GEX / Gamma-flip** — Dealer gamma exposure; the strike where net dealer gamma flips sign.
- **SCD Type 2** — Slowly Changing Dimension tracking historical attribute changes via effective-from/to dates.
- **MD&A** — Management Discussion & Analysis, Item 7 of 10-K/10-Q; the NLP sentiment target.
- **Stale-while-error** — Serving older cached rows when a live refresh fails (existing operational behaviour).
- **Regime** — A tagged macro window (hike / cut / hold / inverted-curve / recession) in `dim_macro_regime`.
