# Roadmap: ML, Experiments & Observability

Living plan for the remaining work on the stock screening platform. Covers the ML overhaul,
the experiments/benchmarks suite, observability, and the smaller gaps. Nothing here is
committed to git yet.

_Last updated: 2026-09-16_

---

## 1. Status snapshot

### Done
- Environment installed and version-pinned; `start-stack.sh` / `stop-stack.sh`.
- NIFTY 500 poller + historical daily backfill (`yfinance`).
- Kafka topics; SeaTunnel ingestion (Kafka -> Iceberg bronze) with **dead-letter routing**.
- Spark technical indicators (bronze -> silver).
- Stock scoring model (Spark MLlib) + evaluation + model selection.
- Druid Kafka ingestion + FastAPI backend + React dashboard (5 pages).
- On-demand pipeline: dashboard **Run pipeline** -> Airflow DAGs + live step status.
- Iceberg maintenance: compaction, manifest rewrite, snapshot expiry (dashboard **Maintenance**).

### Remaining
1. **ML overhaul** (this document, §3) — the weakest link.
2. **Experiments & benchmarks** (§4) — `benchmarks/` is empty.
3. **Observability** (§5) — `monitoring/` is empty.
4. **Spark Structured Streaming** — `spark/streaming/` is empty; deferred by the on-demand design.
5. **ML Phase C** — point-in-time fundamentals in training (accumulate now, add later).
6. **Integration tests** — only unit tests exist (`test_indicators`, `test_features`).
7. **Iceberg schema-evolution / time-travel / partition-evolution demos** — queries documented but not executed.
8. **Scheduled Airflow DAGs** — only on-demand; needed for the "unattended trading day" criterion.
9. **Orphan-file cleanup** — omitted (not in the bundled Iceberg runtime; Spark procedure hangs).
10. **Doc drift** — README/spec still titled "Real-Time" though the platform is on-demand.

---

## 2. ML: diagnosis

Recorded results (`ml/evaluation/results/comparison.md`, test ≈ Apr–Sep 2026, K=20):
IC 0.02–0.04, IC IR 0.2–0.54, P@K 0.44–0.54, turnover 0.28–0.57.

### 2.1 Data (biggest constraint)
- **~2 years of daily bars** → ~500 bars/symbol; after the 200-day warm-up only ~300 usable,
  and after dropping null features only **~83k train rows**. The 21d label loses more at the tail.
- **No fundamentals history** — the model is 100% technical by construction.
- **Survivorship bias** — the universe is the *current* NIFTY 500; delisted/removed names are absent.
- **Single regime**, single time split of only ~95 trading days.

### 2.2 Features & labels (fixable, no new data)
- **Absolute price-level features** (`sma_20/50/200`, `ema_12/26`) mixed with scale-free ratios, and
  **no cross-sectional standardization anywhere** — a pooled model partly learns cross-symbol price scale.
- **Absolute-return label** (`close[t+h]/close[t]-1`) while screening is inherently *relative*.
- No industry/sector feature; heavy collinearity; no feature selection.

### 2.3 Model
- One pooled global model; fixed hyperparameters chosen for memory, not tuned.
- **No scaling for LinearRegression** — likely why "linear" oddly wins 5d IC (fitting a price/size artifact).
- No learning-to-rank / cross-sectional objective; no ensembling.

### 2.4 Evaluation
- Single split; no walk-forward; no significance testing (overlapping horizons need Newey–West).
- "Backtest" = mean forward return of top-K, long-only, **no transaction costs**, overlapping windows.
  Turnover 0.28–0.57 would likely erase the edge net of costs.
- Selected by raw IC, ignoring turnover/cost/stability.

### 2.5 What's genuinely good
Leakage-safe time split with embargo; point-in-time-safe technical features; reproducible Iceberg
dataset; honest reporting vs the rule baseline.

---

## 3. ML overhaul roadmap (full)

**Decisions locked:** full roadmap · cross-sectional **excess-return** label · cost-aware
**long/short** backtest · accumulate fundamentals, add later · **5y** history · **10 bps/side**
with horizon rebalancing · **Spark-only** ranking · **JSON** experiment artifacts.

**Central constraint:** cross-sectional feature normalization must be applied **identically at
training and scoring** (single shared transform module), or the model silently breaks in production.

### Phase 1 — Methodology (no new data; highest ROI)

**1a. Shared transforms + train/serve parity + schema**
- New `ml/feature_engineering/transform.py` (Spark), used by both `build_training.py` and `score_stocks.py`:
  - `add_derived_features` — replace absolute levels with scale-free forms
    (`close/sma_200 - 1`, `sma_50/sma_200 - 1`, `ema_12/ema_26 - 1`, …).
  - `add_cross_sectional_features` — per `trade_date` window: winsorized z-score and/or rank-normal;
    optional sector-demean.
  - `MODEL_FEATURES` constant (derived, scale-free).
- `spark/jobs/build_training.py` — join `industry`; apply transforms; compute **excess-return labels**
  (`fwd_ret − cross_sectional_mean(fwd_ret)` per date); keep raw returns for reporting.
- `spark/jobs/score_stocks.py` — apply the same transforms to the latest cross-section.
- `iceberg/schemas/gold-schema.sql` — rebuild `ml.training_dataset` (industry + normalized features +
  excess + raw labels).

**1b. Label + training refactor**
- `ml/feature_engineering/features.py` — excess-return label helper (pure pandas, unit-tested).
- `ml/training/train_model.py` — normalized inputs; small hyperparameter grid; feature importances;
  drop linear-on-raw-levels.

**1c. Rigorous evaluation**
- `ml/evaluation/metrics.py` — Newey–West IC t-stat / p-value; deflated Sharpe.
- New `ml/evaluation/walk_forward.py` — expanding-window folds with embargo.
- New `ml/evaluation/backtest.py` — top-K long-only and long-short, 10 bps/side, rebalance at the
  label horizon, turnover; net cumulative/annualized return, vol, Sharpe, max drawdown, hit rate
  vs NIFTY and equal-weight universe.
- `ml/evaluation/evaluate.py` — walk-forward metrics, sector-neutral view, select on a risk-adjusted
  **net-of-cost** metric.

**1d. Tests + docs + run**
- `tests/test_features.py` — normalization, excess label, backtest cost math, no-leakage.
- Docs alignment; re-run retrain -> evaluate -> score; record results in `ml/experiments/`.

### Phase 2 — Data (the real unlock)
- Backfill **5y** (`poller/backfill.py --period 5y`), clear the daily topic + consumer group,
  re-ingest, recompute indicators, rebuild dataset, retrain, re-evaluate; compare vs Phase 1.
- New `ml/feature_engineering/asof.py` — point-in-time fundamentals join, implemented now and
  enabled once history accrues (bronze fundamentals are already append-only + timestamped).
- Survivorship: document it; add a robustness run restricted to symbols with full history.

### Phase 3 — Modeling (Spark-only)
- Rank-based learning-to-rank approximation via rank labels + **GBT/RF rank ensemble**.
- Drop weak features via importances; add **regime features** (market breadth/volatility);
  sector-neutral score as a complementary view.

### Phase 4 — Rigor
- Purged K-fold + embargo (López de Prado) alongside walk-forward.
- Deflated Sharpe / multiple-testing correction across models and labels.
- `ml/experiments/` JSON registry (config, seed, metrics, model version) + Iceberg time-travel versioning.
- Extend `comparison.json` and the dashboard **Model** page with t-stat / net Sharpe.

### Checkpoint
After Phase 1, review `ml/experiments/` before proceeding. If methodology fixes don't move the
needle, the 5y backfill (Phase 2) is the next lever.

---

## 4. Experiments & benchmarks (spec §20–§27)

All experiment scripts + recorded results live under `benchmarks/`. Each writes a JSON/CSV artifact
plus a short markdown summary. Prefer reproducible runs (fixed universe subsets, fixed seeds).

### Experiment 1 — Throughput
- **Goal:** how ingestion/processing scales with universe size.
- **Method:** run the pipeline at 50 / 200 / 500 symbols; measure poll duration, records/sec to Kafka,
  SeaTunnel ingest rate, indicator runtime, score runtime.
- **Metrics:** end-to-end wall time per stage; records/sec; Spark stage timings.
- **Output:** `benchmarks/throughput/{50,200,500}.json` + summary chart.

### Experiment 2 — Latency
- **Goal:** end-to-end latency poll -> score queryable in Druid.
- **Method:** timestamp the pipeline steps; poll Druid `screener` until the new snapshot appears;
  repeat across poll cadences (and full vs quick runs).
- **Metrics:** per-stage latency, publish->Druid handoff lag, end-to-end p50/p95.
- **Output:** `benchmarks/latency/*.json` + summary.

### Experiment 3 — Model performance
- **Goal:** rule baseline vs MLlib, honestly.
- **Method:** use the Phase 1 evaluation stack (walk-forward IC + Newey–West, P@K, net-of-cost
  long/short backtest, turnover, deflated Sharpe).
- **Metrics:** IC mean/t-stat, P@K vs universe/index, net Sharpe, max drawdown, turnover.
- **Output:** `benchmarks/model/*.json` + markdown; feeds `ml/experiments/`.

### Experiment 4 — Fault recovery
- **Goal:** recovery + data completeness after component failure (spec §19, FR-12).
- **Method:** kill Kafka / SeaTunnel / Druid / Spark mid-run; restart; verify offsets/checkpoints and
  that no committed data is lost or duplicated (bronze growth vs expected).
- **Metrics:** recovery time, lost/duplicated records, consumer lag after restart.
- **Output:** `benchmarks/recovery/*.md` + JSON.

### Experiment 5 — Iceberg
- **Goal:** schema evolution, time travel, compaction under small-batch writes (spec §11.2).
- **Method:** add a column without rewriting (`ALTER TABLE ADD COLUMN`) and read old data; query a
  past snapshot / `TIMESTAMP AS OF`; run compaction and measure file-count reduction; demonstrate
  partition evolution (add a `days(...)`/`months(...)` partition).
- **Metrics:** file counts before/after, snapshot counts, query correctness across versions.
- **Output:** `benchmarks/iceberg/*.md` + JSON.

### Experiment 6 — Scalability
- **Goal:** effect of Kafka partitions and Spark executors on latency/throughput.
- **Method:** vary topic partition count (recreate topics) and Spark parallelism/executor settings;
  re-run Experiments 1–2.
- **Metrics:** throughput, end-to-end latency, resource usage.
- **Output:** `benchmarks/scalability/*.json` + summary.

### Prerequisites / notes
- Experiments 1, 2, 6 need the stack up and a repeatable driver; make small fixtures (universe limits).
- Experiment 3 depends on the Phase 1 ML stack; Experiment 5 overlaps Phase 1/2 Iceberg demos.
- All runs should record: git commit, config (universe size, cadence), and timestamps for reproducibility.

---

## 5. Observability

`monitoring/` currently empty. Objective: collect operational metrics and surface them (spec §3.2, §21).

### Collection
- **Kafka:** consumer lag per topic/group (`kafka-consumer-groups` / JMX or `kafka-exporter`).
- **Spark:** streaming/batch metrics (task times, shuffle, GC) via the Spark metrics REST/`SparkListener`.
- **Druid:** ingestion lag, segment counts, query latency (`/druid/coordinator/v1`, SQL `sys.*` tables).
- **HDFS:** capacity, block/replication health (`hdfs dfsadmin -report`).
- **Pipeline:** per-run stage durations + success/failure — already available from Airflow; persist to JSON.

### Surfacing
- A lightweight `monitoring/collect_metrics.py` that snapshots the above to `monitoring/metrics/*.json`
  on a schedule (or after each pipeline run).
- Optional dashboard panel: a small "Ops" view in the React app (pipeline last-run, Kafka lag, Druid
  datasource counts) backed by a new `/api/ops` endpoint.
- Keep it dependency-light (no Prometheus/Grafana unless wanted) to match the native-install style.

---

## 6. Smaller gaps
- **Integration tests** — add pipeline/API/Druid e2e smoke tests (start stack -> run quick pipeline ->
  assert Druid snapshot + API responses).
- **Structured Streaming** — optional; only if the on-demand design is revisited.
- **Iceberg orphan-file cleanup** — needs a driver-side implementation or different Iceberg packaging.
- **Data quality** — stale-quote detection and fundamentals-outlier handling beyond dead-letter.
- **Doc drift** — align README/spec titles with the on-demand architecture.

---

## 7. Decisions locked
- ML scope: full roadmap, executed in phases with a checkpoint after Phase 1.
- Label: cross-sectional excess return.
- Backtest: long-only + long-short, 10 bps/side, rebalance at the label horizon.
- Fundamentals: accumulate now, add to training once history exists.
- History: 5 years for training.
- Ranking: Spark-only (rank labels + GBT/RF ensemble).
- Experiment tracking: JSON artifacts under `ml/experiments/` (no MLflow).

---

## 8. Success criteria / checkpoints
- IC stable across walk-forward folds with Newey–West t-stat > ~2 (or an honest "no reliable edge").
- Net-of-cost long-short Sharpe and max drawdown beat the equal-weight universe and the technical baseline.
- Feature importances sensible; embargo honored (leakage tests).
- All six experiments produce reproducible artifacts under `benchmarks/`.
- Observability snapshots per run under `monitoring/`.

---

## 9. Risks
- 2y (then 5y) data caps absolute performance; markets are noisy — aim for a stable, cost-aware edge,
  not a high IC.
- Train/serve parity is the main footgun; mitigated by the single shared transform module.
- Spark MLlib has no native LTR -> approximation via rank labels.
- Cost/slippage assumptions are arbitrary -> parametrize and sensitivity-test (5/10/20 bps).
- Survivorship bias remains unless historical index membership is obtained.
