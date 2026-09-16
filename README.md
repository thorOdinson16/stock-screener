# On-Demand Stock Screening & Scoring Platform

Distributed platform that screens the NIFTY 500 universe: `yfinance` poller -> Kafka ->
SeaTunnel -> HDFS/Iceberg -> Spark (technical indicators) -> Spark MLlib (scoring model) ->
Druid (low-latency ranked queries), driven by a React dashboard and orchestrated by Airflow.

The pipeline is **on-demand** — press **Run pipeline** in the dashboard (or trigger the Airflow
DAG) and it refreshes the whole universe end to end. Druid ingests the serving topics continuously,
so query latency is low even though the batch pipeline is triggered rather than always-on.

Full spec: see `docs/project-spec.md`.

## Pipeline

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

Stages (each is a `scripts/` wrapper run as an Airflow task):

1. **preflight** – check HDFS/Kafka/Druid/SeaTunnel are up and a model is selected
2. **poll** – fetch quotes (and fundamentals on Full runs) via `yfinance`
3. **ingest** – SeaTunnel drains Kafka into Iceberg bronze
4. **indicators** – Spark computes SMA/EMA/RSI/MACD/volatility/momentum into silver
5. **score** – Spark MLlib scores and ranks the latest cross-section into gold
6. **serve** – publish the latest snapshot to Kafka for Druid ingestion
7. **wait_for_druid** – block until Druid is serving the fresh snapshot

## Stack (native install, no Docker for core services)

| Component  | Version                              |
|------------|---------------------------------------|
| Java       | 21 (PATH) / 17 (`JAVA_HOME`)          |
| Kafka      | 4.3.1 (KRaft, no ZooKeeper)           |
| Spark      | 4.1.3 (Scala 2.13.17)                 |
| Hadoop     | 3.4.1                                  |
| Hive       | 4.1.0 (Metastore only)                 |
| SeaTunnel  | 2.3.13 (Zeta engine)                   |
| Iceberg    | 1.11.0 (`iceberg-spark-runtime-4.1_2.13`) |
| Druid      | 37.0.0 (single-server-small, native)   |
| Airflow    | 3.3.1                                  |
| Maven      | 3.8.7                                  |
| Data source | `yfinance` (Yahoo Finance, unofficial, delayed) |

## Starting the stack

```bash
./start-stack.sh   # HDFS -> Hive Metastore -> Kafka -> SeaTunnel -> Druid -> Airflow
./stop-stack.sh     # reverse order teardown
```

`start-stack.sh` also points Airflow's `dags_folder` at this repo and creates the `screening`
pool. Logs: `~/stack-logs/<service>.log`.

- HDFS NameNode UI - http://localhost:9870
- Hive Metastore - thrift://localhost:9083
- Kafka broker - localhost:9092
- SeaTunnel Zeta REST - http://localhost:5801
- Druid console - http://localhost:8888
- Airflow UI - http://localhost:8080

## Running the pipeline

From the dashboard (recommended): **Run pipeline** (Quick = quotes only, or Full = quotes +
fundamentals; optional universe limit and price-history publish) and **Retrain model**. The
UI streams step-by-step status and refreshes the data when the run succeeds.

Or without the UI:

```bash
scripts/run_once.sh --full --history     # or: scripts/run_once.sh --limit 20
```

Or trigger the DAGs directly in the Airflow UI (`screening_on_demand`, `screening_retrain`).
Overlapping runs are prevented by a 1-slot `screening` pool.

## Web dashboard

```bash
./start-ui.sh   # FastAPI :8000 + Vite dashboard :5173
./stop-ui.sh
```

Pages: **Dashboard** (breadth, gainers/losers, sector performance), **Top Picks** (5d/21d),
**Screener** (filter by industry/score/P-E/RSI/momentum; CSV export), **Stock Detail**
(price + SMA overlays, RSI, MACD, fundamentals), **Model** (IC + Newey–West t-stat,
sector-neutral IC, net-of-cost Sharpe vs the rule baseline), **Ops** (Kafka lag, Druid
segments/latency, HDFS health, pipeline runs). Manual refresh by default; toggle 30s
auto-refresh in the sidebar.

See `setup.md` for the full ingestion + ML runbook.

## Configuration

- `config/pipeline.env` — service homes, Spark packages/memory, Druid/Kafka endpoints, defaults
- `config/airflow.env` — Airflow REST credentials; copy from `config/airflow.env.example`
  (password in `~/airflow/simple_auth_manager_passwords.json.generated`); gitignored

## Repository layout

```
poller/           yfinance poller + historical daily-bar backfill
kafka/            Topic definitions + create-topics.sh
seatunnel/        SeaTunnel job configs (Kafka -> Iceberg bronze)
spark/jobs/       Spark batch jobs (indicators, training set, scoring, serving publish)
iceberg/schemas/  Bronze / silver / gold + ML table DDL
ml/               Feature engineering, MLlib training/evaluation, saved models
api/              FastAPI backend (Druid queries + Airflow pipeline trigger)
frontend/         React + Vite dashboard (5 pages)
airflow/dags/     On-demand + retrain DAGs
druid/ingestion/  Druid Kafka supervisor specs + submit.sh
scripts/          Pipeline step wrappers (poll/ingest/indicators/score/serve/retrain)
config/           Pipeline + Airflow connection config
tests/            Unit tests (indicators, features, metrics)
benchmarks/       Throughput/latency/scalability experiments (pending)
docs/             Project specification and design notes
```

## Status

- [x] Environment installed and version-pinned
- [x] Orchestrated start/stop scripts
- [x] NIFTY 500 poller + historical backfill (yfinance)
- [x] Kafka topics + SeaTunnel ingestion (Kafka -> Iceberg bronze)
- [x] Spark technical indicators (bronze -> silver)
- [x] Stock scoring model (Spark MLlib) + evaluation
- [x] Druid ingestion (Kafka supervisors)
- [x] FastAPI backend + React dashboard (5 pages)
- [x] On-demand pipeline: dashboard button -> Airflow DAGs + step status
- [x] Dead-letter routing (SeaTunnel validation -> `market.deadletter`)
- [x] Iceberg maintenance (compaction, manifest rewrite, snapshot expiry)
- [x] ML methodology overhaul (shared scale-free/cross-sectional transforms, excess-return
      labels, walk-forward/purged K-fold IC + Newey–West, cost-aware long/short backtest,
      rank ensemble, `ml/experiments/` registry) — results pending the 5y backfill
- [x] Experiments suite under `benchmarks/` (throughput, latency, model, recovery, Iceberg,
      scalability) — run with the stack up
- [x] Observability (`monitoring/collect_metrics.py` + `/api/ops` + Ops page)
- [ ] Spark Structured Streaming (near-real-time indicators)

## Notes / limitations

- Data comes from `yfinance` (unofficial, delayed) — fine for research, not for trading decisions.
- The ML signal is weak but positive (IC ≈ 0.02–0.05), which is expected for noisy forward returns.
- Single-broker Kafka (`replication.factor=1`) and a single HDFS DataNode: no fault tolerance yet.
- `market.quotes` Kafka retention is 1 day; run the poller to repopulate it before its Druid datasource exists.
