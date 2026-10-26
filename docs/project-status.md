# Project Status & Closure

Status: **delivered (on-demand)** · Last updated: 2026-10-27

This document is the plan-of-record closure for the stock screening platform. It
records what was delivered, the model outcome and the decision taken, what is
deferred/out of scope, and how to run and tear down the stack. It replaces the
earlier `ROADMAP.md`.

---

## 1. What the platform is

A fully **on-demand** NIFTY 500 screening pipeline. Nothing runs continuously:
a run is triggered (dashboard button or Airflow DAG) and refreshes the whole
universe end to end; Druid's Kafka supervisors are kept suspended at rest.

```text
React dashboard ──POST /api/pipeline/run──▶ FastAPI ──REST──▶ Airflow DAG
                                                                  │
  yfinance poller ─▶ Kafka ─▶ SeaTunnel ─▶ Iceberg bronze ─────────┤
                                                                  ▼
        Spark indicators ─▶ silver ─▶ Spark MLlib scoring ─▶ gold
                                                                  │
                     Kafka (market.screener / market.history / market.scores)
                                                                  ▼
                                                        Druid ─▶ dashboard
```

Pipeline stages (each a `scripts/` wrapper run as an Airflow task): `preflight →
druid_resume → poll → ingest → indicators → score → serve → wait_for_druid →
druid_wait → druid_suspend → collect_metrics`.

---

## 2. Delivered

- Environment installed/version-pinned; `./start-stack.sh` / `./stop-stack.sh`.
- NIFTY 500 poller (single on-demand cycle) + historical daily backfill; 5 years
  ingested (562,929 daily bars; 499 symbols + NIFTY index).
- Annual financial statements (`poller/financials.py` → `market.financials` →
  `bronze/silver.financial_statements`): ~4.5 fiscal years per company, refreshed
  on Full runs; used as point-in-time model features.
- Kafka topics + SeaTunnel ingestion (Kafka → Iceberg bronze) with dead-letter routing.
- Spark technical indicators (bronze → silver).
- ML scoring: shared scale-free / cross-sectional transforms with train-serve
  parity, excess-return labels, GBT/RF/Linear + rank models + rank ensemble,
  walk-forward and purged K-fold evaluation, Newey–West IC t-stats, cost-aware
  long/short backtest (10 bps/side), deflated Sharpe, feature importances.
- Druid serving via on-demand supervisors (suspended at rest), incremental
  `price_history` publishing.
- FastAPI backend + React dashboard (6 pages: Dashboard, Top Picks, Screener,
  Stock Detail, Model, Ops), manual refresh only.
- On-demand pipeline with failure propagation (a failed stage fails the task).
- Observability (`monitoring/collect_metrics.py`, `GET/POST /api/ops`, Ops page)
  and data-quality checks (`monitoring/data_quality.py`).
- Experiments suite under `benchmarks/` (throughput, latency, model, recovery,
  Iceberg, scalability) — each writes a JSON + markdown summary.
- Reproducible experiment registry under `ml/experiments/`.
- Docker packaging for Windows/macOS/Linux (`docker-compose.yml`, `docker/`),
  prebuilt images on GHCR (`ghcr.io/thorodinson16/stock-screener-{platform,frontend}`).
  The native install is unchanged.

---

## 3. Model status

**What it does:** scores each stock by how likely it is to beat the average
stock over the next 5 and 21 trading days. Inputs are 14 scale-free technical
features plus 6 point-in-time fundamentals from annual statements (earnings
yield, book-to-price, ROE, debt/equity, revenue growth, log market cap), all
cross-sectionally normalized per day. Targets are cross-sectional excess returns.

**Selected models (2026-10-27 retrain):** `rank_ensemble` (GBT-rank + RF-rank) for
both horizons. Selection now uses walk-forward: the fixed-test winner and the rank
ensemble are both walk-forwarded, and the one with the higher walk-forward IC
t-stat is kept. On the fixed test year alone, `linear` (21d) and `gbt` (5d) looked
best, but their walk-forward t-stats were only 0.52 and 0.42.

**Results (5y data, K=20, 10 bps/side):**

| metric | 5d | 21d |
|---|---|---|
| Walk-forward IC (t) | **0.021 (3.55)** | **0.042 (3.76)** |
| Walk-forward folds positive | 4/5 | 5/5 |
| Walk-forward fold ICs | −0.001, +0.023, +0.034, +0.035, +0.012 | +0.003, +0.030, +0.088, +0.072, +0.013 |
| Walk-forward net Sharpe | 0.76 | 2.24 |
| Fixed-test IC (t) | 0.020 (2.51) | 0.043 (2.41) |
| Fixed-test net Sharpe | 1.49 | 1.04 |
| Full-history (414 symbols) IC (t) | 0.019 (2.23) | 0.036 (2.04) |
| Rule baseline net Sharpe (fixed test) | −1.03 | 0.90 |

Before this round (technical features only, 2026-10-04): walk-forward IC 0.012
(t 1.68) at 5d and −0.001 (t −0.08) at 21d.

**Decision.** Both horizons now clear the pre-agreed walk-forward bar (t > 2), and
the 21d edge is positive in every fold. The gain comes from the fundamentals; the
early folds, which were negative with technical features only, turned positive.
The edge is still modest (IC 0.02–0.04). Caveats: about 15 configurations were
tried; yfinance may serve restated rather than as-first-published statements; the
universe is today's NIFTY 500 (survivorship bias). Full write-up:
`docs/model-research.md`.

**Modelling iterations** (all recorded in `ml/experiments/`):
1. **Kept** — dropped degenerate constant market features (18 → 14 features).
2. **Rejected** — added raw, time-varying market-state features. 21d t = −0.35.
3. **Rejected** — importance-based feature pruning. 21d t = 0.07.
4. **Kept** — point-in-time fundamentals from annual statements, published 90 days
   after fiscal year end (research: 21d ensemble t 1.06 → 3.88).
5. **Rejected** — extra technical features (reversal, 12-1 momentum, volume trend,
   beta / idiosyncratic volatility, max return, illiquidity, sector-relative
   momentum): no gain on top of fundamentals, and less stable folds.
6. **Kept** — select the production model by walk-forward instead of the fixed
   test year.

Not yet pursued: a 63-day horizon. Fundamentals helped most there in the
research screen (linear IC 0.045), but it needs serving and dashboard changes.

---

## 4. Deferred / out of scope

- **Known issue — runs don't add new daily bars.** Indicators and scoring read
  `bronze.quotes_daily`, which only the historical backfill (`poller/backfill.py`)
  fills. A pipeline run's live quotes go to `bronze.market_quotes` and Druid, not
  into the daily bars, so scores stay at the date of the last backfill (currently
  2026-10-01) until the backfill is re-run. Proposed fix: an incremental
  daily-bar fetch + `quotes-daily-job` in each run.
- **Scheduled/timetable Airflow DAGs** — removed: the platform is fully on-demand.
- **Spark Structured Streaming** — removed; there is no always-on stream processor.
- **Iceberg orphan-file cleanup** — omitted (bundled runtime lacks
  `org.apache.iceberg.actions.RemoveOrphanFiles`).
- **Survivorship bias** — the universe is the *current* NIFTY 500; historical
  index membership isn't available. A full-history robustness subset is reported.
- **Kafka partition sweep (Experiment 6)** — not run: partition counts can only
  be increased, so it would permanently alter the topics. Spark parallelism was
  swept instead.

---

## 5. Run it

Prereq: service homes are configured in `config/pipeline.env`.

```bash
# Start everything (HDFS, Hive Metastore, Kafka, SeaTunnel, Druid, Airflow).
# Leaves the Druid supervisors suspended (on-demand).
./start-stack.sh

# Full runbook (ingestion, ML, Druid, troubleshooting): setup.md
#   - service URLs, schema creation, unit tests, retrain/evaluate/score,
#     reset steps and teardown are all documented there.
# Run the UI (FastAPI :8000 + Vite :5173):
./start-ui.sh
./stop-ui.sh

# Stop everything:
./stop-stack.sh
```

Run the pipeline without Airflow: `scripts/run_once.sh --full --history` (or
`--limit 20`). Retrain: dashboard **Retrain model**, or `scripts/retrain.sh`.

Key locations:

- `config/pipeline.env` — service homes, endpoints, defaults.
- `ml/experiments/` — every evaluation run (config, seed, metrics, git commit).
- `ml/evaluation/results/comparison.{json,md}` — latest model comparison.
- `benchmarks/` — experiment scripts + curated results.
- `monitoring/metrics/` and `monitoring/quality/` — operational snapshots.
- `docs/project-spec.md` — original specification.

---

## 6. Verification (as of closure)

- `tests/test_features.py`, `test_indicators.py`, `test_benchmarks.py`,
  `test_data_quality.py`, `test_monitoring.py`, `test_supervisors.py` pass;
  `test_pipeline_e2e.py` is opt-in (`RUN_E2E=1`).
- Frontend `npm run typecheck` clean; Airflow DAGs parse without import errors.
- A full on-demand Airflow run completes, including resume → drain → suspend of
  the Druid supervisors.

---

## 7. Re-pin to the installed stack (2026-10-04)

The machine's service installs were replaced by older releases and could not be
changed, so the project was ported down to them (the README version table is the
source of truth): Spark 3.3.4 / Scala 2.12, Iceberg 1.8.1, Hadoop 3.3.6, Hive 3.1.3,
Kafka 3.9.2, Druid 31.0.1, Airflow 3.3.0. Changes: Spark/Kafka package coordinates;
a Python 3.10 venv for Spark (`spark/.venv`); in-memory Spark catalog and Iceberg
`check-nullability` off in `scripts/lib.sh`; `spark/jobs/nullability.py` (Spark 3.3
rejects nullable data into NOT NULL columns); `LOCATION` clauses removed from the
Iceberg DDL (Hadoop catalog); a KRaft config for Kafka (`kafka/configs/`); per-service
JVMs and unpaused DAGs in `start-stack.sh`; and `evaluate.py` now records `base_algo`
for non-ensemble selections (a latent bug that broke scoring whenever a non-GBT
single model was selected). Data was rebuilt from scratch (HDFS was empty) and the
model retrained; verified with the unit tests, `scripts/run_once.sh --history` and an
Airflow-triggered `screening_on_demand` run via the API. Model metrics in section 3 are from the
later 2026-10-27 retrain (section 8).

---

## 8. Experiments 4 & 6, fundamentals, Docker (2026-10-27)

**Experiment 4 — fault recovery** (`benchmarks/recovery/summary.md`). Each
component was crash-killed (`kill -9`) partway into a pipeline stage, restarted,
and the run retried (50-symbol runs).

| killed | during | run failed at | recovered | Druid check |
|---|---|---|---|---|
| Kafka | ingest | `serving` (first stage that publishes) | yes | one snapshot added, no duplicates |
| SeaTunnel | ingest | `ingest` | yes | one snapshot; consumer backlog drained to 0 |
| Druid MiddleManager | `wait_druid` | did not fail | yes | Druid's `supervise` restarted it; ingestion tasks survived |

Failures surface at the right stage, nothing is half-reported as success, and the
retried run leaves clean counts (one screener snapshot = 499 rows, one score set =
980 rows). The benchmark runner now resumes/suspends the Druid supervisors around a
run, like `scripts/run_once.sh`; without that, `wait_druid` could never succeed.

**Experiment 6 — scalability** (`benchmarks/scalability/summary.md`). 100-symbol
quick runs at Spark `local[2]`, `local[4]` and `local[8]`: 320 s, 317 s and 316 s in
total. Spark stages gain ~20% from 2 → 4 cores and nothing beyond (JVM start-up and
Python UDF overhead dominate at this size). **The bottleneck is Druid:** ~191 s of
every run (~60%) is waiting for Druid ingestion tasks to start and serve the new
snapshot, constant across configurations; the yfinance poll is another ~55 s.

**Fundamentals and model selection.** See section 3 and `docs/model-research.md`.
Also fixed: `asof.py` failed on multi-symbol panels (merge_asof needs a globally
sorted key).

**Docker.** The platform runs under Docker Compose with prebuilt GHCR images; see
`docker/README.md`. The images were rebuilt with this round's changes and verified
(upgrade of an existing install, 20-symbol data load, Full run via the dashboard
API) before being pushed. `docker/init.sh` re-applies topic/table definitions whenever
they change, so an upgraded image adds the new financial-statement tables and
topic to an existing install.

