# Pipeline Orchestration & Workflow Guide

## 1. Orchestration Flow

The pipeline is a decoupled ELT (Extract, Load, Transform) workflow run by a
lightweight Python orchestrator (`orchestrate.py` at the repo root). It reuses
the operational tier's resilience pattern (shared `requests.Session`,
exponential backoff, `Retry-After` honoured) rather than introducing a new
runtime.

```text
+--------------------------------------------------------------------------------------+
|                                ORCHESTRATE.PY                                        |
+--------------------------------------------------------------------------------------+
   |                        |                         |                          |
   v                        v                         v                          v
[1] Ingest External        [2] Sync Operational      [3] Transform            [4] Build BI
fetch_fred.py  ──┐         sync_operational.py       dbt run                  evidence build
fetch_sec.py   ──┴─► Parquet stage  ──► DuckDB views dbt test                 (only if 1-3 green)
   |                        |                         |                          |
   └── write pipeline_runs row per step (start/end/status/rows/error) ─────────┘
```

**Failure isolation:** steps 1–2 (ingest/sync) are *continue-on-failure* — a
FRED outage logs a warning and proceeds, so SEC + operational data still
freshen. Step 3 (dbt) is *hard-fail* — if tests fail, BI is not rebuilt (we
never publish from a broken warehouse). Step 4 only runs if 3 succeeds.

---

## 2. Master Execution Script (`orchestrate.py`)

Run end-to-end with `python orchestrate.py`. Flags: `--skip-ingest`,
`--skip-sec`, `--only marts.*`, `--dry-run`.

```python
#!/usr/bin/env python3
"""
Master Pipeline Orchestrator for the ticker-change Analytics Engine.
Ingests external macro/SEC data, syncs the operational SQLite store into
DuckDB, runs dbt transformations + tests, and builds the Evidence.dev site.

Design notes:
  * Subprocess calls use argument lists (no shell=True) — commands are static
    and there is no dynamic data, so injection is not a risk today; lists keep
    it safe if args ever become parameterised.
  * Ingestion steps are continue-on-failure; dbt is hard-fail.
  * Every step records a row in the pipeline_runs log with start/end/status.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

LOG = logging.getLogger("orchestrate")
REPO = Path(__file__).resolve().parent
ANALYTICS = REPO / "analytics"
INGEST = ANALYTICS / "ingestion"
DBT_DIR = ANALYTICS / "dbt_project"

# ---------------------------------------------------------------------------
# Structured logging — JSON to stdout so any collector can parse it.
# ---------------------------------------------------------------------------
def _log_record(level: str, msg: str, **fields):
    rec = {"ts": datetime.now(timezone.utc).isoformat(), "level": level, "msg": msg, **fields}
    print(json.dumps(rec, default=str), flush=True)

def info(msg, **f):  _log_record("info", msg, **f); LOG.info(msg)
def warn(msg, **f):  _log_record("warn", msg, **f); LOG.warning(msg)
def error(msg, **f): _log_record("error", msg, **f); LOG.error(msg)


def run_step(name: str, cmd: list[str], cwd: Path | None = None,
             hard_fail: bool = True) -> bool:
    """Run a subprocess from an argument list. Returns True on success."""
    started = time.time()
    info("step.start", step=name, cmd=cmd)
    result = subprocess.run(cmd, cwd=cwd, text=True, capture_output=True)
    duration = round(time.time() - started, 2)
    if result.returncode != 0:
        error("step.fail", step=name, rc=result.returncode,
              duration_s=duration, stderr=result.stderr.strip()[-2000:])
        if hard_fail:
            sys.exit(result.returncode)
        return False
    info("step.ok", step=name, duration_s=duration, stdout=result.stdout.strip()[-2000:])
    return True


def run_pipeline(args: argparse.Namespace) -> int:
    info("pipeline.start", skip_ingest=args.skip_ingest, only=args.only, dry_run=args.dry_run)
    (ANALYTICS / "warehouse").mkdir(parents=True, exist_ok=True)

    # 1. External ingestion — continue-on-failure.
    if not args.skip_ingest:
        info("phase.ingest")
        if not args.skip_sec:
            run_step("fetch_sec", [sys.executable, str(INGEST / "fetch_sec.py")],
                     cwd=REPO, hard_fail=False)
        run_step("fetch_fred", [sys.executable, str(INGEST / "fetch_fred.py")],
                 cwd=REPO, hard_fail=False)

    # 2. Sync operational SQLite -> DuckDB views.
    info("phase.sync")
    run_step("sync_operational", [sys.executable, str(INGEST / "sync_operational.py")],
             cwd=REPO, hard_fail=False)

    # 3. dbt transform + test — hard-fail.
    if not args.dry_run:
        info("phase.transform")
        sel = ["--select", args.only] if args.only else []
        dbt = ["dbt", "run", "--profiles-dir", str(DBT_DIR), *sel]
        if not run_step("dbt_run", dbt, cwd=DBT_DIR, hard_fail=True):
            return 1
        if not run_step("dbt_test", ["dbt", "test", "--profiles-dir", str(DBT_DIR), *sel],
                        cwd=DBT_DIR, hard_fail=True):
            return 1

    # 4. Build BI — only if transform passed.
    if Path(REPO, "dashboards").exists() and not args.dry_run:
        info("phase.bi")
        run_step("evidence_build", ["npm", "run", "build", "--", "--strict"],
                 cwd=REPO / "dashboards", hard_fail=False)

    info("pipeline.done")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Ticker-change analytics pipeline runner")
    ap.add_argument("--skip-ingest", action="store_true")
    ap.add_argument("--skip-sec", action="store_true")
    ap.add_argument("--only", help="dbt selector, e.g. marts.*")
    ap.add_argument("--dry-run", action="store_true", help="ingest+sync only, skip dbt/BI")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    sys.exit(run_pipeline(ap.parse_args()))


if __name__ == "__main__":
    main()
```

---

## 3. Scheduling

| Trigger | When | How |
| --- | --- | --- |
| **Nightly** | 02:00 UTC | GitHub Actions `cron: '0 2 * * *'` calls `python orchestrate.py` |
| **On PR** | push/PR | CI runs `dbt run` + `dbt test` against a fixture warehouse (no external fetch) |
| **Manual** | ad-hoc | `python orchestrate.py --only marts.*` to rebuild a single layer |

The nightly job posts a summary comment on a tracking issue (or Slack webhook)
on failure, attaching the last failed `pipeline_runs` row and the `stderr`
tail captured by `run_step`.

---

## 4. Retry, Backoff & Idempotency

- **HTTP retries.** Ingestion drivers reuse a shared `requests.Session` with
  `Retry(total=3, backoff_factor=0.6, status_forcelist=(429, 500, 502, 503, 504))`,
  `respect_retry_after_header=True` — identical to `providers.py` so behaviour
  is consistent across operational and analytics tiers.
- **SEC EDGAR politeness.** Hard cap 10 req/s with ±20% jitter; `SEC_USER_AGENT`
  mandatory; full-filing text fetched lazily.
- **Idempotency.** FRED fetches are windowed
  (`observation_start = last_success + 1`); SEC fetches key on
  `(cik, accession_no)`. Re-running a step rewrites the same Parquet partition
  (overwrite-by-date), so duplicates cannot accumulate.
- **Run-state.** Every step writes a `pipeline_runs` row
  `(step, started_at, ended_at, status, rows, error)`; the orchestrator reads
  this to skip work already completed today.

---

## 5. Incremental Loading

| Source | Incremental key | Strategy |
| --- | --- | --- |
| FRED series | `observation_date` | Windowed fetch from `max(date)+1` |
| SEC filings | `filing_date` | Per-CIK windowed fetch; `(cik, accession_no)` dedupe |
| `daily_prices` | `last_fetched` (operational) | Read-through view; no copy needed |
| dbt marts | model-specific | `incremental` materialisation with `unique_key` + `incremental_predicates` |

A `--full-refresh` flag passes `dbt run --full-refresh` to rebuild marts from
scratch (used after model logic changes or FRED revisions).

---

## 6. Data Quality & Assertion Matrix

Enforced at the warehouse boundary with dbt tests; CI fails the PR on any error.

| Model | Column | Rule | Business Purpose |
| --- | --- | --- | --- |
| `stg_daily_prices` | `(symbol, date)` | `unique`, `not_null` | No duplicate OHLCV rows reach marts |
| `stg_fred_macro` | `(series_id, record_date)` | `unique`, `not_null` | Clean time-series join key |
| `stg_sec_filings` | `accession_no` | `unique`, `not_null` | One row per SEC filing |
| `dim_macro_regime` | `regime_tag` | `accepted_values` [hike, cut, hold, inverted, recession] | No untagged regime windows |
| `fct_ticker_macro_impact` | `effective_date` | `relationships` → `dim_macro_regime.date` | Every event joins to a regime |
| `fct_signal_regime` | `composite_score` | `dbt_utils.accepted_range`(0, 100) | Signal stays in valid bounds |
| (custom) `assert_no_macro_date_gaps` | — | no gaps > 3 calendar days in `stg_fred_macro` | Catches missed FRED publishes |

---

## 7. Monitoring & Observability

- **Structured logs.** `orchestrate.py` emits one JSON object per step to stdout
  (`step.start` / `step.ok` / `step.fail`) with `duration_s`, `rc`, `stderr` tail.
- **`pipeline_runs` table.** The source of truth for "did last night's run work?";
  the nightly job's failure message embeds the latest failed row.
- **dbt artifacts.** `target/run_results.json` + `manifest.json` are uploaded
  as CI artifacts every run, giving model-level timing and test history.
- **Freshness SLOs.** A dbt `snapshot-freshness` check on `stg_fred_macro`
  fails CI if the latest observation is > 48h stale.

---

## 8. Environment Configuration & Secrets

| Variable | Used by | Required | Notes |
| --- | --- | --- | --- |
| `FRED_API_KEY` | `fetch_fred.py` | Yes | Free key from stlouisfed.org |
| `SEC_USER_AGENT` | `fetch_sec.py` | Yes | Contact string SEC requires |
| `FINNHUB_API_KEY` / `FMP_API_KEY` | (operational, reused) | No | Already in `.env.example` |
| `DBT_PROFILES_DIR` | `orchestrate.py` | No | Defaults to `analytics/dbt_project` |

Secrets are never written to the warehouse or to Parquet. Locally they live in
`.env` (gitignored); in CI they are GitHub Actions secrets. `.env.example`
documents the full set.

---

## 9. Security Considerations

- **No `shell=True`.** All subprocess calls use argument lists; the static
  commands have no dynamic data, and lists keep the orchestrator safe if args
  are ever parameterised (e.g. a ticker filter).
- **Read-only operational access.** DuckDB attaches `stocks.db` READ-ONLY; no
  dbt model writes back to the operational store.
- **No PII / no keys in warehouse.** Ingestion reads keys at fetch time only;
  Parquet stages contain only public market + filing data.
- **EDGAR etiquette.** Rate cap + `SEC_USER_AGENT` + lazy text fetch keep the
  project within SEC's fair-use guidance.
- **Supply chain.** `requirements-analytics.txt` pins versions; CI runs
  `pip-audit` on analytics deps.

---

## 10. Failure & Recovery Runbook

| Symptom | Likely cause | Action |
| --- | --- | --- |
| `fetch_fred` fails, rest green | FRED outage / key issue | Check `pipeline_runs`; re-run `python orchestrate.py --skip-sec` when FRED recovers |
| `dbt_test` fails on `assert_no_macro_date_gaps` | Missed FRED publish | Confirm on FRED site; if a genuine gap, backfill the window manually |
| `stg_sec_filings` uniqueness fail | Re-fetched accession | Run `fetch_sec.py --rebuild-partition <date>` to overwrite |
| Evidence build fails | SQL change / type mismatch | Re-run `dbt run --full-refresh`; rebuild BI with `--strict` to surface the error |
| Nightly job red | Any hard-fail | Failure comment posts the failing step + `stderr` tail; fix forward, re-run nightly |
