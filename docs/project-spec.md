# On-Demand Stock Screening & Scoring Platform

## 1. Project Overview

### 1.1 Project Title

**Real-Time Stock Screening and Scoring Platform Using a Distributed Big Data Stack**

### 1.2 Project Summary

This project designs and implements a scalable, fault-tolerant financial data platform that ingests
price and fundamentals data for the NIFTY 500 stock universe on demand, maintains a historical
lakehouse, computes technical and fundamental screening signals, scores stocks with a distributed
machine-learning model, and exposes ranked "good pick" results through low-latency analytical
queries.

The platform simulates a research desk re-evaluating a broad stock universe on demand rather than a
single ticker. When a run is triggered, price and fundamentals snapshots are pulled from a public
market data source, transported through Apache Kafka, ingested and transformed using Apache
SeaTunnel, stored on HDFS using Apache Iceberg as the lakehouse table format, processed using Apache
Spark (batch), scored using Spark MLlib, and exposed through Apache Druid for fast on-demand ranking
and dashboards.

Apache Airflow orchestrates the on-demand pipeline (poll -> ingest -> indicators -> score -> serve)
along with model retraining and Iceberg maintenance. Nothing runs continuously: the Druid Kafka
supervisors are suspended at rest and resumed only for the duration of a run.

The system is designed not merely as a collection of Big Data technologies, but as an integrated
distributed system where each component has a clearly defined responsibility.

---

# 2. Problem Statement

Retail and semi-professional investors evaluating a broad universe like the NIFTY 500 face a
volume problem: manually screening 500 stocks against technical and fundamental criteria is slow,
error-prone, and quickly stale as prices move. Most free screening tools either cover a narrow
watchlist or refresh infrequently, and combining technical signals (momentum, volatility, moving
averages) with fundamental signals (valuation, earnings, size) into one ranked view is rarely
available on demand.

The proposed system addresses this by building a distributed platform capable of:

- ingesting price and fundamentals data for a 500-stock universe on demand;
- performing distributed data ingestion and transformation;
- maintaining large-scale historical price/fundamentals data;
- computing technical indicators and fundamental ratios after each run;
- scoring and ranking stocks using a combined technical + fundamental model;
- supporting fast, on-demand "give me today's best picks" queries over historical and recent data;
- handling failures (API outages, malformed data) and recovering from processing interruptions;
- measuring system performance under increasing data volume.

---

# 3. Objectives

## 3.1 Primary Objectives

1. Build a complete end-to-end stock screening pipeline.
2. Implement high-throughput ingestion using Kafka.
3. Use SeaTunnel for data ingestion and transformation.
4. Store large-scale price/fundamentals data on HDFS.
5. Use Apache Iceberg as the lakehouse table format on HDFS.
6. Implement on-demand batch processing using Apache Spark.
7. Develop a distributed stock-scoring model combining technical and fundamental signals using
   Spark MLlib.
8. Provide low-latency, on-demand ranked-query access using Apache Druid.
9. Use Airflow to orchestrate the on-demand pipeline, fundamentals refresh, retraining and maintenance.
10. Evaluate scalability, latency, throughput, fault tolerance, and model performance.

## 3.2 Secondary Objectives

- Demonstrate schema evolution (e.g. adding a new fundamental ratio).
- Demonstrate data-quality validation (missing fundamentals, stale prices, halted stocks).
- Support historical backfills of price history.
- Handle duplicate or out-of-order snapshots from the polling source.
- Demonstrate recovery from component failures.
- Compare processing performance at different universe sizes.
- Provide system observability and operational metrics.
- Maintain reproducibility of experiments.

---

# 4. High-Level Architecture

```text
                         ┌─────────────────────┐
                         │   Market Data        │
                         │      Source           │
                         │                       │
                         │  yfinance poller —     │
                         │  NIFTY 500 universe    │
                         └──────────┬────────────┘
                                    │
                                    ▼
                         ┌─────────────────────┐
                         │      Apache Kafka     │
                         │  market.quotes         │
                         │  market.fundamentals   │
                         │  market.scores          │
                         │  market.deadletter       │
                         └──────────┬────────────┘
                                    │
                                    ▼
                         ┌─────────────────────┐
                         │   Apache SeaTunnel     │
                         │  validate / normalize   │
                         │  route invalid records   │
                         └──────────┬────────────┘
                                    │
                       ┌────────────┴────────────┐
                       ▼                          ▼
              ┌─────────────────┐        ┌─────────────────┐
              │  HDFS + Iceberg   │        │   Dead Letter     │
              │  bronze/silver/   │        │   dataset          │
              │  gold tables      │        │                    │
              └────────┬──────────┘        └────────────────────┘
                       │
                       ▼
              ┌─────────────────────┐
              │ Spark Structured      │
              │ Streaming              │
              │ technical indicators,  │
              │ feature engineering    │
              └────────┬──────────────┘
                       │
                       ▼
              ┌─────────────────────┐
              │   Spark MLlib          │
              │  stock scoring model    │
              └────────┬──────────────┘
                       │
                       ▼
              ┌─────────────────────┐
              │   Apache Druid          │
              │  ranked queries,         │
              │  dashboards               │
              └─────────────────────┘

              ┌─────────────────────┐
              │   Apache Airflow        │
              │  polling schedule,       │
              │  fundamentals refresh,   │
              │  Iceberg maintenance,    │
              │  model retraining         │
              └─────────────────────┘
```

---

# 5. Technology Stack

| Layer                     | Technology                              |
|----------------------------|------------------------------------------|
| Data source                | `yfinance` (Yahoo Finance, unofficial)    |
| Messaging / streaming       | Apache Kafka 4.3.1 (KRaft)                |
| Ingestion / transformation  | Apache SeaTunnel 2.3.13 (Zeta engine)      |
| Distributed storage         | Apache Hadoop HDFS 3.4.1                   |
| Lakehouse table format      | Apache Iceberg 1.11.0                      |
| Metastore                  | Apache Hive Metastore 4.1.0                 |
| Batch processing            | Apache Spark 4.1.3 (batch)                 |
| Machine learning            | Spark MLlib                                |
| Low-latency analytics        | Apache Druid 37.0.0                        |
| Orchestration               | Apache Airflow 3.3.1                        |
| Build tooling                | Maven 3.8.7                                 |

> **Note on data source**: `yfinance` wraps Yahoo Finance's unofficial, publicly-accessible
> endpoints. Data is typically delayed (~15 min for NSE symbols on the free tier) and there is no
> formal SLA or usage contract. This is appropriate for an academic/personal project; it should
> not be relied on for production trading decisions.

---

# 6. Stock Market Data

The platform operates on real price and fundamentals data for the NIFTY 500 universe (~500 NSE
tickers, `.NS` suffix), polled on a schedule rather than streamed tick-by-tick.

## 6.1 Core Quote Schema

```text
symbol
timestamp
open
high
low
close
volume
previous_close
change_percent
market_cap
sector
industry
exchange
```

## 6.2 Core Fundamentals Schema

Refreshed less frequently than quotes (fundamentals change slowly — daily is sufficient).

```text
symbol
timestamp
pe_ratio
pb_ratio
eps
dividend_yield
market_cap
beta
fifty_two_week_high
fifty_two_week_low
return_on_equity
debt_to_equity
revenue_growth
earnings_growth
```

## 6.3 Derived Features

The streaming pipeline computes, per symbol:

```text
sma_20 / sma_50 / sma_200            (simple moving averages)
ema_12 / ema_26                       (exponential moving averages)
rsi_14                                 (relative strength index)
macd / macd_signal                     (moving average convergence divergence)
volatility_20d                          (rolling stddev of returns)
volume_avg_20d
volume_ratio                            (current volume vs 20-day average)
distance_from_52w_high
distance_from_52w_low
price_momentum_1m / 3m / 6m
```

These technical features combine with the fundamentals schema (§6.2) to feed the scoring model.

---

# 7. Data Ingestion (Poller)

A poller fetches the current universe and publishes snapshots to Kafka. The platform is
on-demand: a run triggers exactly one poll cycle, which fetches the universe once and exits —
there is no continuous polling loop.

The poller must support:

- configurable stock universe (default: NIFTY 500 constituent list);
- an optional universe cap (`--universe-limit`) for testing / smaller runs;
- quotes always, fundamentals optional (Full runs);
- batched requests to stay within yfinance's soft rate limits;
- retry with backoff on transient API failures;
- graceful handling of delisted/halted/missing symbols;
- reproducible runs for testing (fixed symbol subset).

Example:

```text
Universe: NIFTY 500 (~500 symbols)
Quote poll: one cycle per run
Fundamentals poll: included on Full runs
Batch size per request: 50 symbols
```

The universe size and Full/quick mode are tunable so downstream behavior can be tested at
different scales.

---

# 8. Kafka Layer

Kafka acts as the primary event-streaming layer, decoupling the poller from downstream
consumers.

## 8.1 Responsibilities

Kafka will:

- receive quote and fundamentals snapshots from the poller;
- buffer incoming snapshots;
- decouple the poller from downstream consumers;
- provide partitioned parallelism across the stock universe;
- provide replay capability (e.g. reprocessing a day's quotes after a bug fix);
- retain events for a configurable period.

## 8.2 Topic Design

```text
market.quotes
market.fundamentals
market.scores
market.deadletter
```

The primary quotes topic should be partitioned using:

```text
symbol
```

so that all snapshots for a given stock land on the same partition, in order — needed for
correct rolling-window feature computation (moving averages, RSI, etc.) per symbol.

Partition count should be evaluated experimentally against the 500-symbol universe size and
target throughput.

---

# 9. SeaTunnel Layer

SeaTunnel performs ingestion and transformation between Kafka and the storage layer.

## Responsibilities

- schema mapping (raw yfinance JSON → normalized schema);
- data normalization (currency, timestamp timezone — NSE trades in IST);
- field validation (missing price, non-numeric fundamentals, out-of-range values);
- type conversion;
- filtering invalid records (e.g. a symbol returned no data this poll);
- routing invalid records to a dead-letter dataset;
- ingestion into the data lake;
- configurable transformations.

Invalid records must not silently disappear — they are routed to a dead-letter dataset/topic for
inspection.

```text
Kafka
  │
  ▼
SeaTunnel
  │
  ├── Valid ──────► HDFS / Iceberg
  │
  └── Invalid ────► Dead Letter
```

---

# 10. HDFS Storage Layer

HDFS provides the distributed, durable storage layer underneath Iceberg. It stores:

- raw ingested quote and fundamentals snapshots (bronze);
- cleaned, validated, feature-enriched data (silver);
- daily scored/ranked output (gold);
- model artifacts and training datasets.

---

# 11. Apache Iceberg Lakehouse

## 11.1 Table Layers

```text
bronze.quotes_raw
bronze.fundamentals_raw
silver.quotes_enriched          (technical indicators added)
silver.fundamentals_clean
gold.stock_scores                (daily ranked scores)
gold.top_picks                    (materialized top-N view)
```

## 11.2 Iceberg Features to Demonstrate

- schema evolution (adding a new technical indicator or fundamental field without breaking
  readers);
- time travel (comparing a symbol's score history, or replaying "what would today's picks have
  looked like using last week's data");
- partition evolution (e.g. partitioning `gold.stock_scores` by date, potentially adding sector
  as a second partition dimension later);
- compaction / file maintenance given many small per-poll writes;
- snapshot expiry.

---

# 12. Batch Processing

Processing runs on demand as one batch job per stage:

```text
Read from Iceberg bronze (quotes_daily, market_fundamentals)
      │
      ▼
Parse + validate
      │
      ▼
Compute rolling technical indicators (per-symbol)
      │
      ▼
Join with latest fundamentals
      │
      ▼
Write to Iceberg silver
```

## Required capabilities

- per-symbol rolling aggregation (moving averages, RSI, volatility);
- duplicate/out-of-order safety (dedupe on `(symbol, trade_date)`) for re-run backfills;
- idempotent writes to Iceberg (overwrite / delete-by-date), so a run can be retried;
- join of quotes with the latest fundamentals snapshot.

Spark Structured Streaming is intentionally out of scope: the platform is fully
on-demand, so there is no always-on stream processor.

---

# 13. Screening Rules (Baseline)

Before the ML model, a rule-based baseline provides an interpretable starting point.

Example rules:

```text
IF pe_ratio < sector_average_pe
AND price_momentum_1m > 0
AND rsi_14 BETWEEN 40 AND 70
THEN candidate

IF close > sma_50
AND sma_50 > sma_200
AND volume_ratio > 1.2
THEN momentum_candidate

IF distance_from_52w_high < 5%
AND earnings_growth > 0
THEN breakout_candidate
```

Rule-based screening provides a transparent, explainable baseline the ML model's rankings can be
compared against.

---

# 14. Spark MLlib

Spark MLlib implements the distributed scoring model.

## Candidate Approaches

- Learning-to-rank / regression predicting forward N-day return (label from historical data);
- Gradient-Boosted Trees regressor scoring a composite "quality" signal;
- Logistic Regression / Random Forest classifying "outperform vs underperform" over a forward
  window;
- K-Means clustering to group similar stocks (momentum cluster, value cluster, etc.) as a
  complementary view alongside scoring.

The final approach should be selected based on experimental results — this is inherently a
weaker-signal problem than fraud classification (markets are noisy), so evaluation must be
honest about limited predictive power rather than optimizing for a misleadingly high accuracy
number.

## ML Pipeline

```text
Iceberg Silver
      │
      ▼
Feature Engineering
(technical + fundamental features)
      │
      ▼
Label Construction
(forward N-day return, from historical price data)
      │
      ▼
Training Dataset
      │
      ▼
Spark MLlib
      │
      ▼
Model Training
      │
      ▼
Model Evaluation
      │
      ▼
Selected Model
      │
      ▼
Stock Score / Rank
```

---

# 15. Model Evaluation

Because this predicts noisy forward returns rather than a rare binary event, evaluation differs
from a fraud-detection setup.

Primary metrics:

- Information Coefficient (rank correlation between predicted score and realized forward return);
- Precision@K (of the top-K ranked picks, how many outperformed a benchmark, e.g. NIFTY 500
  index return, over the forward window);
- Mean Absolute Error / RMSE (if predicting a continuous return);
- Backtested cumulative return of a simulated top-K portfolio vs. benchmark;
- Turnover (how much the top-K list changes poll-to-poll — excessive churn is a practical
  problem even if scores are accurate).

Special attention should be given to:

> **Out-of-sample, time-based validation** — never train on future data relative to the
> evaluation window, since stock data is heavily autocorrelated in time and a naive random
> train/test split will silently leak information and overstate performance.

---

# 16. Druid Analytics Layer

Apache Druid provides low-latency, on-demand queries over the scored/ranked stock universe.

### Market Metrics

- Current price, change %, volume by symbol
- Sector/industry aggregates (average P/E, average momentum)
- Top gainers / losers
- Market-wide breadth (advancing vs declining count)

### Screening Metrics

- Top-K ranked picks (on demand, by current score)
- Score distribution across the universe
- Score trend over time per symbol
- Filterable screens (e.g. "top picks with P/E < 20 and momentum > 0")

### Sector / Portfolio Metrics

- Picks grouped by sector
- Historical hit-rate of past top-K picks (did they outperform the benchmark?)
- Score volatility per symbol (how stable is a stock's ranking over time)

---

# 17. Airflow Orchestration

Airflow orchestrates the on-demand pipeline (and maintenance/retrain), rather than running an
always-on engine.

Example DAG:

```text
                 On-Demand Stock Screening Pipeline

                        START
                          │
                          ▼
                 Preflight + resume Druid supervisors
                          │
                          ▼
                 Poll quotes (and fundamentals on Full)
                          │
                          ▼
                 SeaTunnel -> Iceberg bronze
                          │
                          ▼
                 Spark indicators -> silver
                          │
                          ▼
                 Spark MLlib scoring -> gold
                          │
                          ▼
                 Publish snapshot + history (incremental)
                          │
                          ▼
                 Wait for Druid, then suspend supervisors
                          │
                          ▼
                         END
```

Model retraining and Iceberg maintenance are separate on-demand DAGs.

---

# 18. Data Quality

- missing/null fields in a poll response (delisted, halted, or temporarily unavailable symbol);
- stale quotes (timestamp far older than expected — API returned cached data);
- fundamentals outliers (e.g. negative P/E from negative earnings — valid but needs handling, not
  silently dropped);
- duplicate snapshots from retried polls;
- schema drift if yfinance changes its response shape.

---

# 19. Fault Tolerance

## Kafka
Broker restart recovery; consumer group rebalancing; topic replay for reprocessing.

## Spark
Batch jobs are re-runnable: Iceberg writes are idempotent (overwrite / delete-by-date), so an
interrupted stage can be safely retried.

## HDFS
NameNode/DataNode recovery; replication factor tuning.

## Iceberg
Snapshot rollback if a bad write corrupts `gold.stock_scores`; time travel to recover a known-good
state.

## Poller
Retry with backoff on yfinance API failures; partial-batch failure should not block the rest of
the universe's poll for that cycle.

---

# 20. Scalability Testing

Vary:

- universe size (50 → 200 → 500 symbols);
- Kafka partition count;
- Spark executor count / parallelism.

Measure how each affects end-to-end latency (poll → score available in Druid) and resource usage.

---

# 21. Performance Metrics

## Ingestion
Poll cycle duration; API failure rate; records/sec published to Kafka.

## Kafka
Producer/consumer throughput; consumer lag.

## Spark
Micro-batch processing time; end-to-end latency (event time → processed time).

## HDFS
Write throughput; storage growth rate.

## ML
Training time; inference latency for scoring the full universe.

## Druid
Query latency for top-K ranking queries at increasing data volume.

---

# 22. Security Considerations

- No real brokerage credentials or trading capability — this is a read-only screening/research
  tool, not an execution system.
- Rate-limit-respecting access to the public data source to avoid IP blocking.
- No PII is collected (no real user accounts in this iteration).

---

# 23. Deployment

Native install on a single development machine (no Docker for core services): Kafka, Spark,
Hadoop, Hive Metastore, SeaTunnel, Druid, and Airflow all run as local processes, orchestrated by
`start-stack.sh` / `stop-stack.sh`.

---

# 24. Project Structure

```text
stock-screening-platform/
│
├── docker/                       (reserved — currently native install)
│
├── poller/
│   ├── poller.py
│   ├── universe/                 (NIFTY 500 constituent list)
│   └── config/
│
├── kafka/
│   ├── topics/
│   └── configs/
│
├── seatunnel/
│   └── configs/
│
├── spark/
│   ├── streaming/
│   ├── batch/
│   └── jobs/
│
├── iceberg/
│   ├── schemas/
│   ├── tables/
│   └── maintenance/
│
├── ml/
│   ├── feature_engineering/
│   ├── training/
│   ├── evaluation/
│   └── models/
│
├── airflow/
│   └── dags/
│
├── druid/
│   ├── ingestion/
│   └── queries/
│
├── monitoring/
│
├── tests/
│
├── benchmarks/
│
├── docs/
│
├── start-stack.sh
├── stop-stack.sh
└── README.md
```

---

# 25. Functional Requirements

## FR-01 — Data Polling
System polls quotes and fundamentals for the configured universe on independent schedules.

## FR-02 — Streaming Ingestion
Kafka receives and buffers all polled snapshots.

## FR-03 — Data Transformation
SeaTunnel validates, normalizes, and routes invalid records to dead-letter.

## FR-04 — Distributed Storage
All snapshots persist to HDFS.

## FR-05 — Lakehouse Management
Iceberg maintains bronze/silver/gold tables with schema evolution and time travel.

## FR-06 — Batch Processing
Spark computes technical indicators per symbol in an on-demand batch job.

## FR-07 — Screening
Rule-based baseline screens are available and explainable.

## FR-08 — Machine Learning
Spark MLlib produces a stock score per symbol per run.

## FR-09 — Analytics
Druid serves ranked top-K and filtered screening queries with low latency.

## FR-10 — Orchestration
Airflow schedules fundamentals refresh, retraining, and maintenance.

## FR-11 — Data Quality
Invalid, missing, or stale data is detected and handled, not silently dropped.

## FR-12 — Recovery
The system recovers from component failure without data loss for already-committed data.

---

# 26. Non-Functional Requirements

- End-to-end latency from poll to Druid-queryable score should be measured and bounded.
- The system should tolerate the loss of any single native service and recover on restart.
- Storage growth should be sustainable for at least one full year of 5-minute-interval NIFTY 500
  polling (bounded via Iceberg compaction/retention policy).
- The system should be reproducible: a fixed historical window should always produce the same
  computed features and scores.

---

# 27. Key Experiments

## Experiment 1 — Throughput
Measure Kafka/Spark throughput as universe size scales from 50 to 500 symbols.

## Experiment 2 — Latency
Measure end-to-end latency (poll → score available in Druid) at varying polling frequencies.

## Experiment 3 — Model Performance
Compare rule-based baseline vs. MLlib model using Information Coefficient, Precision@K, and
backtested return vs. benchmark.

## Experiment 4 — Fault Recovery
Kill Kafka/Spark/Druid mid-pipeline and measure recovery time and data completeness.

## Experiment 5 — Iceberg
Demonstrate schema evolution, time travel, and compaction under many small per-run writes.

## Experiment 6 — Scalability
Vary partition count and executor count; measure effect on latency and throughput.

---

# 28. Success Criteria

- The on-demand pipeline runs reproducibly end to end on a single trigger.
- Top-K picks are queryable in Druid within a bounded latency after each run.
- The MLlib-based ranking demonstrably outperforms (or is honestly shown not to outperform) the
  rule-based baseline and the NIFTY 500 benchmark in backtest.
- The system recovers from a killed component without manual data repair.
- All 31 architecture components are demonstrated working together at least once end-to-end.

---

# 29. Expected Final Demonstration

A live run against the actual NIFTY 500 universe, showing: quotes flowing through Kafka, Iceberg
tables growing, Spark computing indicators, Druid serving a ranked top-10 picks query, and the
on-demand Airflow DAG run visible in its UI.

---

# 30. Future Extensions

- Multiple data sources for cross-validation (reduce single-source risk from relying on one
  unofficial API).
- Sector-relative scoring instead of universe-wide scoring.
- Backtesting UI for historical "what would this have picked" exploration.
- Alerting when a stock newly enters/exits the top-K.

---

# 31. Final Architecture Principle

Each component has one clearly defined responsibility: Kafka moves data, SeaTunnel validates and
routes it, Iceberg/HDFS store it durably, Spark computes over it, MLlib scores it, Druid serves it
fast, and Airflow keeps the whole thing running on schedule without manual intervention.
