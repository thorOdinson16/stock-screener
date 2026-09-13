# Kafka Topic Design

## Topics

| Topic                  | Partitions | Replication | Retention | Key            | Purpose |
|-------------------------|------------|-------------|-----------|-----------------|---------|
| `market.quotes`          | 16         | 1           | 1 day     | `symbol`        | Price/volume snapshots from the poller (high volume, frequent) |
| `market.quotes.daily`    | 16         | 1           | 7 days    | `symbol`        | Historical daily OHLCV bars, one-off backfills (see `poller/backfill.py`) |
| `market.fundamentals`     | 4          | 1           | 7 days    | `symbol`        | P/E, EPS, market cap, etc. (low volume, daily refresh) |
| `market.scores`           | 4          | 1           | 7 days    | `symbol`        | Output of the scoring model, per poll cycle |
| `market.deadletter`        | 2          | 1           | 30 days   | none (round-robin) | Records that failed validation in SeaTunnel |

Replication factor is 1 across the board since this is a single-broker deployment — no
replication is possible until a second broker is added. This is a known single point of failure;
`docs/project-spec.md` §19 (Fault Tolerance) should note this explicitly when we get to the
recovery experiments.

## Why `symbol` as the partition key

All three data topics are keyed by `symbol` so that every snapshot for a given stock lands on the
same partition, in the order it was produced. This matters because:

- Spark Structured Streaming's per-symbol rolling-window features (moving averages, RSI,
  volatility) require in-order processing per symbol — if `AAPL`-equivalent snapshots could land
  on different partitions, ordering guarantees break and windowed aggregation would need
  cross-partition coordination instead of simple per-key state.
- Consumer parallelism still works fine: with 16 partitions and ~500 symbols, each partition
  handles the full history of ~30 symbols independently.

## Why 16 partitions for `market.quotes` specifically

This is the highest-volume topic (a snapshot per symbol per poll cycle, on a 1-5 minute cadence
per the poller design in §7). 16 gives headroom for the scalability experiments in §20 (which ask
us to vary Spark executor/task parallelism) without any real overhead cost at this scale — we're
running one broker, nowhere near the partition-count ceiling where metadata/file-handle overhead
would start to matter.

`market.fundamentals` and `market.scores` get 4 partitions since they're much lower-throughput:
fundamentals refresh once a day, scores are produced once per poll cycle already aggregated
per-symbol (not per-tick). `market.deadletter` gets 2 — it should rarely have meaningful traffic,
and when it does, order doesn't matter (each dead-letter record is independent), so no key is
used and Kafka round-robins across partitions.

## Retention rationale

- **`market.quotes`: 1 day.** Kafka retention here is a buffer, not the system of record — every
  valid snapshot is durably persisted to Iceberg bronze within seconds by the SeaTunnel/Spark
  pipeline. 1 day of Kafka retention is enough to allow reprocessing after a same-day bug fix or
  brief consumer outage, without Kafka's own disk usage growing unbounded (500 symbols x
  ~1 snapshot/5min x 1 day is a small, bounded footprint).
- **`market.fundamentals` / `market.scores`: 7 days.** Lower volume, and a longer window is useful
  while actively developing/debugging the pipeline (can replay a week of runs without needing to
  fall back to Iceberg).
- **`market.deadletter`: 30 days.** Deliberately much longer — the whole point of a dead-letter
  topic is to inspect what went wrong later, so it shouldn't evict before anyone's had a chance to
  look. Volume should be low enough that this is cheap.
- **`market.quotes.daily`: 7 days.** Backfilled in bulk (one record per symbol per trading day for
  the requested period, e.g. ~500 symbols x ~500 days). The 7-day window is enough to replay or
  re-ingest after a bug fix without re-running the slow yfinance backfill; once landed in
  `bronze.quotes_daily`, Kafka is only a transport again.