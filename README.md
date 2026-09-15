# Real-Time Stock Screening & Scoring Platform

Distributed platform that continuously screens the NIFTY 500 universe: yfinance poller -> Kafka ->
SeaTunnel -> HDFS/Iceberg -> Spark Structured Streaming (technical indicators) -> Spark MLlib
(scoring model) -> Druid (fast ranked queries), orchestrated by Airflow.

Full spec: see `docs/project-spec.md`.

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

Logs: `~/stack-logs/<service>.log`
Service endpoints once running:

- HDFS NameNode UI - http://localhost:9870
- Hive Metastore - thrift://localhost:9083
- Kafka broker - localhost:9092
- SeaTunnel Zeta REST - http://localhost:5801
- Druid console - http://localhost:8888
- Airflow UI - http://localhost:8080

## Repository layout

```
poller/           yfinance-based scheduled poller for the NIFTY 500 universe
kafka/            Topic definitions, broker/producer/consumer configs
seatunnel/        SeaTunnel job configs (Kafka -> HDFS/Iceberg ingestion)
spark/            Structured Streaming jobs (technical indicators), batch jobs
iceberg/          Table schemas, DDL, maintenance (compaction, expiry) scripts
ml/               Feature engineering, MLlib training/evaluation, saved models
api/              FastAPI backend serving Druid (dashboard BFF)
frontend/         React + Vite dashboard (5 pages)
airflow/dags/     Orchestration DAGs (polling schedule, fundamentals refresh, maintenance)
druid/            Ingestion specs, example ranking queries
monitoring/       Metrics/observability config
tests/            Unit and integration tests
benchmarks/       Throughput/latency/scalability experiment scripts + results
docs/             Project specification and design notes
```

## Status

- [x] Environment installed and version-pinned
- [x] Orchestrated start/stop scripts
- [x] Repo skeleton
- [x] Project spec (pivoted to stock screening/scoring)
- [x] Kafka topic design + creation
- [x] NIFTY 500 poller (yfinance)
- [x] SeaTunnel ingestion (Kafka -> Iceberg bronze)
- [x] Spark technical indicators (bronze -> silver)
- [x] Stock scoring model (Spark MLlib)
- [x] Druid ingestion + FastAPI + React dashboard (5 pages)
- [ ] Spark Structured Streaming (near-real-time indicators)
- [ ] Airflow DAGs
- [ ] Experiments (throughput, latency, fault recovery, Iceberg, scalability, model performance)

## Web dashboard

```bash
./start-ui.sh   # FastAPI :8000 + Vite dashboard :5173
./stop-ui.sh
```

Serves the Druid `screener`, `stock_scores`, `price_history` and `market_quotes`
datasources: market overview, ranked top picks, a filterable screener, stock
detail with price/indicator charts, and the model-evaluation report.

The Dashboard header has a **Run pipeline** button (and a **Retrain model**
action). Both trigger Airflow DAGs (`screening_on_demand`, `screening_retrain`)
via the Airflow REST API and stream step-by-step status back to the UI. Configure
`config/airflow.env` (copy from `config/airflow.env.example`) first. See
`setup.md` for the ingestion steps that populate Druid.
