# Project Status & Closure

Status: **delivered (on-demand)** · Last updated: 2026-09-16

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

---

## 3. Model status

**What it does:** scores each stock by how likely it is to beat the average
stock over the next 5 and 21 trading days. Inputs are 14 scale-free technical
features, cross-sectionally normalized per day; targets are cross-sectional
excess returns.

**Selected models (2026-10-04 retrain):** `rank_ensemble` (5d: `gbt_rank` + `rf_rank`),
`linear` (21d). Selection is noisy: for 21d, `rank_ensemble` had a marginally higher IC
(0.061 vs 0.057) but `linear` was picked.

**Results (5y data, K=20, 10 bps/side):**

| metric | 5d (rank_ensemble) | 21d (linear) |
|---|---|---|
| Fixed-test IC (t) | 0.027 (3.16) | 0.057 (2.63) |
| Fixed-test net Sharpe | 1.93 | 3.19 |
| Walk-forward IC (t) | 0.012 (1.68) | −0.001 (−0.08) |
| Walk-forward net Sharpe | −0.10 | 0.74 |
| Full-history (414 symbols) IC (t) | 0.027 (3.06) | 0.054 (2.38) |
| Rule baseline net Sharpe (fixed test) | −1.03 | 0.90 |

Purged K-fold was not part of the retrain run, so it is not reported here.

**Decision (honest weak edge).** The models rank acceptably on the held-out recent
year, but the edge is **not stable across time**. 21d walk-forward fold ICs are
+0.008, −0.049, −0.072, +0.068, +0.046 and the pooled t-stat (−0.08) is far below the
pre-agreed significance bar (> 2); 5d walk-forward (t = 1.68) also misses it. The 21d
fixed-test Sharpe (3.19) is flattered by one favourable year and should not be
trusted; the walk-forward figures are the honest ones. At 21d the rule baseline
(Sharpe 0.90) is not clearly beaten on a walk-forward basis. The modelling work is
closed with this result.

**Modelling iterations tried** (all recorded in `ml/experiments/`):
1. **Kept** — dropped degenerate constant market features (18 → 14 features).
   21d walk-forward t = 0.41.
2. **Rejected** — added raw, time-varying market-state features. 21d t = −0.35.
3. **Rejected** — importance-based feature pruning. 21d t = 0.07.

The remaining levers (new silver features such as short-term reversal / volume
trend / sector-relative momentum / beta, a longer 63d label, regime-segmented
models, or per-fold hyperparameter re-tuning) were assessed as heavier work with
uncertain payoff and were **not pursued** under the closing decision.

---

## 4. Deferred / out of scope

- **Point-in-time fundamentals (`ml/feature_engineering/asof.py`)** — implemented
  and unit-tested, but not enabled in training; blocked until Full runs accrue
  fundamentals history. The model is technical-only by construction.
- **Scheduled/timetable Airflow DAGs** — removed: the platform is fully on-demand.
- **Spark Structured Streaming** — removed; there is no always-on stream processor.
- **Iceberg orphan-file cleanup** — omitted (bundled runtime lacks
  `org.apache.iceberg.actions.RemoveOrphanFiles`).
- **Survivorship bias** — the universe is the *current* NIFTY 500; historical
  index membership isn't available. A full-history robustness subset is reported.
- **Experiment 4 (fault recovery) and Experiment 6 (scalability)** — scripts are
  provided but were not executed; Experiments 1, 2, 3 and 5 were run.

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
Airflow-triggered `screening_on_demand` run via the API. Section 3 reflects the post-retrain
metrics; the iteration t-stats listed there (0.41, −0.35, 0.07) are from the earlier
pre-retrain data and are kept as history.
