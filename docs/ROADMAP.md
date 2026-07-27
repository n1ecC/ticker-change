# Engineering Roadmap & Sprint Plan

## Sprint Breakdown Overview

```
Week:   1 ─────────── 2 ─────────── 3 ─────────── 4 ─────────── 5
        ├─ Sprint 1 ──────────┤
        │  OLAP integration   ├─ Sprint 2 ──────────┤
        │                     │  FRED + SEC ingest  ├─ Sprint 3 ──────────┤
        │                     │                     │  dbt marts + tests  ├─ Sprint 4 ───┤
        │                     │                     │                     │  BI + orch  │
        └─────────────────────┴─────────────────────┴─────────────────────┴─────────────┘
```

### Cross-cutting work (runs every sprint, not a separate slot)
- **CI**: GitHub Actions workflow runs `dbt run` + `dbt test` on a synthetic
  fixture warehouse on every PR.
- **Testing**: every ingestion driver ships unit tests with mocked HTTP
  (responses/VCR) and a `--dry-run` flag.
- **Observability**: structured JSON logs + a `pipeline_runs` log table.
- **Reproducibility**: `requirements-analytics.txt` pins DuckDB/dbt/Evidence versions.

---

## Sprint 1 — Operational-to-OLAP Integration (Weeks 1–2)

**Goal:** Stand up the DuckDB warehouse, attach the live `stocks.db` read-only,
and prove end-to-end connectivity with a single staging model.

**Scope**
- Initialise `analytics/warehouse.duckdb`.
- Enable `sqlite_scanner` + `httpfs` extensions; `ATTACH 'stocks.db'` (READ-ONLY).
- Scaffold `analytics/dbt_project/` (`dbt_project.yml`, `profiles.yml`, `sources.yml` declaring `daily_prices`, `api_cache`, `tickers`, `app_settings`).
- Write `stg_daily_prices` (cast `date` to DATE, dedupe on `symbol,date`).

**Deliverables (files)**
- `analytics/warehouse/init.sql` — extension + attach script.
- `analytics/dbt_project/dbt_project.yml`, `profiles.yml`, `models/staging/stg_daily_prices.sql`, `models/sources.yml`.
- `analytics/requirements-analytics.txt`.
- `Makefile` targets: `make warehouse`, `make dbt-run`, `make dbt-test`.

**Acceptance Criteria**
- [ ] `SELECT COUNT(*) FROM stg_daily_prices` matches `stocks.db` within a freshness delta.
- [ ] `dbt run` + `dbt test` pass against a fixture `stocks.db`.
- [ ] No write path back to `stocks.db` (verify with a read-only file mode test).
- [ ] CI workflow green on a bare PR.

**Dependencies / Risks**
- Dep: DuckDB ≥ 0.10 with working `sqlite_scanner`.
- Risk: SQLite type affinity surprises → mitigated by explicit casts in staging.

---

## Sprint 2 — External Ingestion Pipelines (Weeks 2–3)

**Goal:** Resilient, idempotent ingestion of FRED macro series and SEC EDGAR
filings into Parquet staging.

**Scope**
- `fetch_fred.py`: pull `CPIAUCSL`, `DFF`, `T10Y2Y`, `DGS10`, `DGS2` with
  windowed incremental fetch (`observation_start = last_run + 1`), backoff
  reusing the operational `Retry` pattern, write partitioned Parquet by series.
- `fetch_sec.py`: resolve CIKs via `company_tickers.json`, pull submissions per
  CIK, filter 10-K/10-Q/8-K, fetch metadata + (lazily) filing text; 10 req/s
  cap + jitter; `SEC_USER_AGENT` enforced.
- `sync_operational.py`: mirror `daily_prices` + relevant `api_cache` payloads
  into DuckDB views (read-through, not copy, where possible).
- `pipeline_runs` log table + per-run `ingested_at`, `source_url`, `row_count`.

**Deliverables**
- `analytics/ingestion/{fetch_fred.py, fetch_sec.py, sync_operational.py}`.
- `analytics/ingestion/_http.py` (shared session/retry, mirroring `providers.py`).
- `analytics/ingestion/_state.py` (run-state log + idempotency keys).
- Tests: `tests/test_fetch_fred.py`, `tests/test_fetch_sec.py` (mocked HTTP).

**Acceptance Criteria**
- [ ] Re-running `fetch_fred.py` with no new data performs zero network fetches.
- [ ] FRED series back-fill (10y) completes in < 60s and writes one Parquet per series.
- [ ] SEC fetch respects 10 req/s (verified by mocked clock in tests).
- [ ] `pipeline_runs` records start/end/status/row_count for every run.

**Dependencies / Risks**
- Dep: `FRED_API_KEY` provisioned; `SEC_USER_AGENT` set.
- Risk: EDGAR text volume → mitigated by lazy text fetch (metadata first).

---

## Sprint 3 — dbt Data Modeling & Dimensional Marts (Weeks 3–4)

**Goal:** Publish a clean Kimball star schema with macro-regime context and
the first comparative metrics.

**Scope**
- Staging: `stg_fred_macro`, `stg_sec_filings`, `stg_api_cache_fundamentals`.
- Intermediate: `int_macro_regime` (tag hike/cut/hold/inverted/recession from
  EFFR + 2s10s), `int_ticker_macro_aligned` (temporal range join on effective
  date — AS-OF join).
- Marts:
  - `dim_companies` (SCD Type 2 on ticker/CIK/name changes).
  - `dim_macro_regime` (dated regime windows).
  - `fct_ticker_macro_impact` (macro metrics on each corporate-action event date).
  - `fct_signal_regime` (existing buyer-signal composite joined to regime).
- Tests: `unique` + `not_null` on all PKs; `relationships` on FKs;
  `accepted_values` on regime tags; custom test for no macro-event date gaps.

**Deliverables**
- `models/staging/*.sql`, `models/intermediate/*.sql`, `models/marts/*.sql`.
- `models/marts/*.yml` (schema + tests + docs).
- `analyses/regime_conditional_volatility.sql` (reusable analysis).
- `tests/assert_no_macro_date_gaps.sql`.

**Acceptance Criteria**
- [ ] `dbt run --select marts.*` succeeds; `dbt test` passes 100%.
- [ ] Lineage graph shows `daily_prices → stg → int → fct` with no orphan models.
- [ ] `fct_signal_regime` reproduces a known signal composite within ±0 (deterministic).
- [ ] Every mart document block states sample size + the regime it covers.

**Dependencies / Risks**
- Dep: Sprint 2 Parquet stages populated.
- Risk: AS-OF join correctness → mitigated by a property test comparing to a pandas reference.

---

## Sprint 4 — Presentation Tier & Pipeline Orchestration (Weeks 4–5)

**Goal:** Ship the BI layer and the automated end-to-end orchestrator.

**Scope**
- Scaffold Evidence.dev under `dashboards/`; connect to `warehouse.duckdb`.
- Pages: Regime Heatmap, Signal-vs-Regime, GEX-vs-Curve, Corporate-Action Velocity.
- `orchestrate.py` master runner (see `docs/ORCHESTRATION.md`): ingest → sync →
  dbt run → dbt test → build BI; structured logging, run-state, failure
  isolation, idempotent re-run.
- Optional Prefect deployment spec for nightly scheduling.
- Reproduction guide: `docs/REPRODUCE.md`.

**Deliverables**
- `dashboards/` Evidence project + `package.json`.
- `orchestrate.py` (repo root).
- `.github/workflows/nightly-pipeline.yml` (cron) + `ci.yml`.
- `docs/REPRODUCE.md`, updated `README.md` analytics-engine section.

**Acceptance Criteria**
- [ ] `python orchestrate.py` runs end-to-end on a clean clone in < 8 min.
- [ ] Re-running it performs incremental work only (no full refetch).
- [ ] Evidence site builds and renders all four pages from real warehouse data.
- [ ] Nightly cron fires and posts a Slack/PR-comment summary on failure.

**Dependencies / Risks**
- Dep: Sprint 3 marts published.
- Risk: Evidence SQL limits for GEX → fallback Streamlit page stubbed if needed.

---

## Risk Register (programme-level)

| ID | Risk | Owner | Trigger | Mitigation |
| --- | --- | --- | --- | --- |
| R1 | EDGAR IP block | Sprint 2 | 403/429 spike | Rate cap + jitter + Parquet cache |
| R2 | Operational schema drift | Sprint 1/3 | `db.py` change | Staging casts + CI fixture test |
| R3 | NLP cost/latency | Sprint 3 | Score latency > 2s/filing | Offline scoring, persist results |
| R4 | Causal over-claim | Sprint 3/4 | Any "causes" wording | CI grep test + CI sample-size gate |
| R5 | Reproducibility rot | All | Unpinned deps | `requirements-analytics.txt` pinning |

---

## Milestones & Definition of Done

- **M1 (end S1):** Warehouse attaches live `stocks.db`; first staging model green in CI.
- **M2 (end S2):** Nightly macro+SEC ingest produces Parquet stages with a clean run log.
- **M3 (end S3):** dbt marts published, 100% test pass, signal composite reproduces.
- **M4 (end S4):** Evidence dashboards live, `orchestrate.py` runs end-to-end < 8 min, repro guide merged.

**Definition of Done (every ticket):** code merged, tests green in CI, dbt docs
updated, no `now()`/non-determinism in models, acceptance criteria checked off.
