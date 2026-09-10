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
- [ ] Kafka topic design + creation
- [ ] NIFTY 500 poller (yfinance)
- [ ] SeaTunnel ingestion (Kafka -> Iceberg bronze)
- [ ] Spark Structured Streaming (technical indicators, bronze -> silver)
- [ ] Stock scoring model (Spark MLlib)
- [ ] Druid ingestion + ranked-query dashboards
- [ ] Airflow DAGs
- [ ] Experiments (throughput, latency, fault recovery, Iceberg, scalability, model performance)
