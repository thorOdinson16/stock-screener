# Setup & Operations

Runbook for the on-demand stock screening platform.

- **Section 1 — Fresh setup** (one-time): do this once on a new machine/checkout.
- **Section 2 — Daily / on-demand runs**: the repeat workflow after setup.
- **Reference & troubleshooting**: verification queries, benchmarks, reset, etc.

This assumes all the big-data components are already **installed and configured**
(Java, Spark, Hadoop/HDFS, Hive, Kafka, SeaTunnel, Druid, Airflow, Maven — see the
version table in `README.md`). Service homes and endpoints are read from
`config/pipeline.env`. The only remaining local setup is the poller's Python
virtualenv; the API venv and dashboard `node_modules` are created automatically
by `start-ui.sh`.

---

## Prerequisites (assumed installed)

### One-time shell setup

```bash
# 1. Service homes + endpoints. Edit config/pipeline.env if your install paths
#    differ from the defaults, then source it. start-stack.sh does NOT source it.
source config/pipeline.env

# 2. Airflow REST credentials for the dashboard's "Run pipeline" button.
cp config/airflow.env.example config/airflow.env
#    Fill AIRFLOW_PASSWORD from ~/airflow/simple_auth_manager_passwords.json.generated
#    (skip this if you will drive runs from scripts/run_once.sh or the Airflow UI.)

# 3. Make the entrypoint scripts executable.
chmod +x start-stack.sh stop-stack.sh kafka/topics/create-topics.sh scripts/*.sh
```

Internet access is needed for `yfinance` (Yahoo Finance / NSE) and for Spark to
download its Ivy packages on first use.

### Spark + Iceberg command template

Define this once per shell and reuse it for every `spark-sql` command below. It
avoids repeating the four Iceberg `--conf` lines.

```bash
SPARK_ICEBERG=(
  --packages org.apache.iceberg:iceberg-spark-runtime-3.3_2.12:1.8.1
  --conf spark.sql.extensions=org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions
  --conf spark.hadoop.hive.metastore.uris=thrift://localhost:9083   # the CLI always opens a Hive client; use the running metastore, not embedded Derby
  --conf spark.sql.catalog.iceberg=org.apache.iceberg.spark.SparkCatalog
  --conf spark.sql.catalog.iceberg.type=hadoop
  --conf spark.sql.catalog.iceberg.warehouse=hdfs://localhost:9000/warehouse
  --conf spark.sql.defaultCatalog=iceberg
  --conf spark.sql.session.timeZone=UTC
)
# usage: spark-sql "${SPARK_ICEBERG[@]}" -e "..."
```

---

# 1. Fresh setup (one-time)

## 1.1 Start the stack

```bash
source config/pipeline.env   # if not already sourced
./start-stack.sh
```

Starts HDFS -> Hive Metastore -> Kafka -> SeaTunnel -> Druid -> Airflow. Logs go
to `~/stack-logs/`. `start-stack.sh` also points Airflow's `dags_folder` at this
repo, creates the `screening` pool, and leaves the Druid supervisors suspended
(on-demand). Verify with `jps` and `ss -tln`.

- HDFS NameNode UI — http://localhost:9870
- Hive Metastore — thrift://localhost:9083
- Kafka broker — localhost:9092
- SeaTunnel Zeta REST — http://localhost:5801
- Druid console — http://localhost:8888
- Airflow UI — http://localhost:8080

## 1.2 Create Kafka topics

```bash
./kafka/topics/create-topics.sh
```

Creates `market.quotes`, `market.quotes.daily`, `market.fundamentals`,
`market.scores`, `market.screener`, `market.history`, `market.deadletter`.

## 1.3 Create the Iceberg catalog and tables

Namespaces, bronze, silver and gold/ML tables must exist before any job writes to
them. `build_training.py` recreates `ml.training_dataset` itself if its schema
changes; the other tables are created here once.

```bash
# Namespace (the bronze/silver/gold schema files create their own namespaces too).
spark-sql "${SPARK_ICEBERG[@]}" -e "CREATE NAMESPACE IF NOT EXISTS bronze;"

spark-sql "${SPARK_ICEBERG[@]}" -f iceberg/schemas/bronze-schema.sql
spark-sql "${SPARK_ICEBERG[@]}" -f iceberg/schemas/silver-schema.sql
spark-sql "${SPARK_ICEBERG[@]}" -f iceberg/schemas/gold-schema.sql
```

Creates `silver.quotes_enriched`, `silver.fundamentals_clean`,
`gold.stock_scores`, `gold.top_picks`, `ml.training_dataset`.

Verify:

```bash
spark-sql "${SPARK_ICEBERG[@]}" -e "SHOW TABLES IN bronze; SHOW TABLES IN silver; SHOW TABLES IN gold; SHOW TABLES IN ml;"
```

## 1.4 Register the Druid supervisors

The platform is on-demand: supervisors are registered once and then left
**suspended**. `start-stack.sh` re-suspends them after every restart, and each
pipeline run resumes/drains/suspends them automatically.

```bash
./druid/ingestion/submit.sh
scripts/druid_supervisors.sh status   # expect state=SUSPENDED for each
```

## 1.5 Set up the poller virtualenv

```bash
cd poller
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Spark 3.3.4 cannot run on Python 3.12, so the Spark jobs use a separate Python 3.10
virtualenv (`config/pipeline.env` and `scripts/lib.sh` point `spark.pyspark.python` at it):

```bash
/usr/local/bin/python3.10 -m venv spark/.venv
spark/.venv/bin/pip install "numpy<2" "pandas<2.2" "pyarrow<15"
```

One-time machine settings for the installed (older) releases:

- Kafka 3.9.2 runs in KRaft mode from `kafka/configs/server.properties`. Format its
  log dir once: `kafka-storage.sh format -t $(kafka-storage.sh random-uuid) -c kafka/configs/server.properties`.
- Druid 31 bundles a ZooKeeper whose admin server grabs port 8080 (Airflow's port).
  Add `admin.enableServer=false` to `$DRUID_HOME/conf/zk/zoo.cfg`.
- Spark jobs run with an in-memory Spark catalog (`spark.sql.catalogImplementation=in-memory`,
  set in `scripts/lib.sh`); the Iceberg catalog is a Hadoop catalog on HDFS, so Hive is
  not needed by Spark. The interactive `spark-sql` CLI always opens a Hive client, so
  `SPARK_ICEBERG` points it at the running metastore.

(The API venv and dashboard `node_modules` are created automatically by
`start-ui.sh` in step 1.10.)

## 1.6 Load historical data

The live poller captures only the current snapshot, so load 5 years of daily bars
once — this is what makes `sma_200`, momentum and 52-week features meaningful.
The benchmark index (`^NSEI`) rides the same pipeline and is needed for
index-relative metrics.

```bash
cd poller && source .venv/bin/activate

# Quick smoke test first (20 symbols).
python backfill.py --once --universe-limit 20

# Full universe + benchmark index, 5 years (~10 min).
python backfill.py --once --include-index --period 5y
```

Optionally publish one live quote snapshot as well:

```bash
python poller.py --once --quotes-only
```

Backfills are re-runnable; the indicator job dedupes `(symbol, trade_date)`.

If the NIFTY 500 bars are already ingested and you only need to add the index,
publish just the index instead of re-sending the whole universe:

```bash
cd poller && source .venv/bin/activate
$KAFKA_HOME/bin/kafka-topics.sh --delete --topic market.quotes.daily --bootstrap-server localhost:9092
sleep 5 && ./kafka/topics/create-topics.sh
python backfill.py --once --index-only --period 5y
```

## 1.7 Ingest to Iceberg bronze (SeaTunnel)

These are **BATCH** jobs: each drains its Kafka topic from the consumer group's
committed offset, commits to Iceberg, then exits. Valid records go to bronze;
invalid records are routed to `market.deadletter`.

```bash
$SEATUNNEL_HOME/bin/seatunnel.sh --config seatunnel/configs/quotes-daily-job.conf     # daily bars
$SEATUNNEL_HOME/bin/seatunnel.sh --config seatunnel/configs/quotes-job.conf           # live quotes snapshot
$SEATUNNEL_HOME/bin/seatunnel.sh --config seatunnel/configs/fundamentals-job.conf     # fundamentals
```

Each job prints a result table and exits. Commits are visible in
`~/seatunnel/logs/seatunnel-engine-server.log` (`do commit table`).

## 1.8 Compute silver indicators

Reads `bronze.quotes_daily`, computes the technical indicators
(`docs/project-spec.md` §6.3), and overwrites `silver.quotes_enriched` +
`silver.fundamentals_clean`.

```bash
spark-submit \
  --packages org.apache.iceberg:iceberg-spark-runtime-3.3_2.12:1.8.1 \
  spark/jobs/compute_indicators.py
```

## 1.9 Train and select the model

Rebuilds the training dataset, trains all models, evaluates them, and writes
`ml/models/selected.json` (required by `preflight` before any run). See
`docs/project-status.md` for the current model outcome.

```bash
# Build the training dataset (features, excess-return labels, benchmarks, split).
spark-submit --driver-memory 4g --master "local[8]" \
  --packages org.apache.iceberg:iceberg-spark-runtime-3.3_2.12:1.8.1 \
  spark/jobs/build_training.py

# Train all candidate models.
spark-submit --driver-memory 6g --master "local[8]" \
  --packages org.apache.iceberg:iceberg-spark-runtime-3.3_2.12:1.8.1 \
  ml/training/train_model.py

# Evaluate, select, and record the run (walk-forward + cost-aware backtest).
spark-submit --driver-memory 4g --master "local[8]" \
  --packages org.apache.iceberg:iceberg-spark-runtime-3.3_2.12:1.8.1 \
  ml/evaluation/evaluate.py
```

Or simply run all three: `scripts/retrain.sh`.

Artifacts: `ml/models/<algo>_<label>_<timestamp>/`, `ml/models/registry.json`,
`ml/models/selected.json`, `ml/evaluation/results/comparison.{json,md}`, and a
record under `ml/experiments/`.

## 1.10 Configure Airflow and start the UI

```bash
# Already done in Prerequisites: config/airflow.env holds the REST credentials.
./start-ui.sh
```

- Dashboard — http://localhost:5173
- API docs — http://localhost:8000/docs

Stop with `./stop-ui.sh`. To avoid the launcher holding your terminal, run it
detached: `setsid ./start-ui.sh >/tmp/ui.log 2>&1 < /dev/null &`.

## 1.11 First pipeline run

From the dashboard, press **Run pipeline** (Quick or Full, optional universe
limit). Or without the UI:

```bash
scripts/run_once.sh --full --history   # or: scripts/run_once.sh --limit 20
```

Or trigger `screening_on_demand` in the Airflow UI (http://localhost:8080).

## 1.12 Verify the fresh setup

```bash
# Kafka has messages
$KAFKA_HOME/bin/kafka-console-consumer.sh \
  --bootstrap-server localhost:9092 --topic market.quotes --from-beginning --max-messages 5

# Bronze has rows
spark-sql "${SPARK_ICEBERG[@]}" -e "SELECT COUNT(*) FROM bronze.market_quotes; SELECT * FROM bronze.market_quotes LIMIT 5;"

# Silver rows + warm-up null counts (see "Verify silver" in the Reference)
spark-sql "${SPARK_ICEBERG[@]}" -e "SELECT COUNT(*) AS rows, COUNT(DISTINCT symbol) AS symbols FROM silver.quotes_enriched;"

# Unit tests (no Spark needed)
python tests/test_indicators.py
python tests/test_features.py
```

For the full bronze/silver/Iceberg verification queries, see
**Reference & troubleshooting** below.

---

# 2. Daily / on-demand runs

## 2.1 Start the stack

```bash
source config/pipeline.env
./start-stack.sh
```

Druid supervisors come up **suspended** — nothing ingests until a run.

## 2.2 Run the pipeline

Three equivalent ways:

- **Dashboard**: press **Run pipeline** (Quick = quotes only, or Full = quotes +
  fundamentals; optional universe limit and price-history publish).
- **CLI**: `scripts/run_once.sh [--full] [--history] [--limit N]`.
- **Airflow UI**: trigger `screening_on_demand`.

Stages (each a `scripts/` wrapper run as an Airflow task):

1. `preflight` — services up and a model is selected
2. `druid_resume` — resume the suspended Druid supervisors
3. `poll` — fetch quotes (and fundamentals on Full runs)
4. `ingest` — SeaTunnel drains Kafka into Iceberg bronze
5. `indicators` — Spark computes indicators into silver
6. `score` — Spark MLlib scores/ranks the latest cross-section into gold
7. `serve` — publish the snapshot (history is published incrementally)
8. `wait_for_druid` — block until Druid serves the fresh snapshot
9. `druid_wait` / `druid_suspend` — drain, then suspend the supervisors
10. `collect_metrics` — snapshot operational metrics

## 2.3 Retrain the model

Do this periodically (e.g. after a backfill or a few weeks of new data):

```bash
scripts/retrain.sh
```

Or press **Retrain model** in the dashboard.

## 2.4 Start / stop the UI

```bash
./start-ui.sh
./stop-ui.sh
```

## 2.5 Post-run checks

```bash
# API health + datasource row counts
curl -s http://localhost:8000/api/health | python3 -m json.tool

# Druid is serving a fresh screener snapshot
scripts/druid_supervisors.sh status      # all SUSPENDED, lag 0
```

## 2.6 Routine upkeep

```bash
# Iceberg compaction / manifest rewrite / snapshot expiry
scripts/maintenance.sh                    # SNAPSHOT_RETENTION_DAYS=30 ...
# Operational metrics snapshot (also runs as the DAG's last task)
python3 monitoring/collect_metrics.py
# Data-quality checks (duplicates, stale quotes, fundamental outliers)
python3 monitoring/data_quality.py
```

See **Reference & troubleshooting** for benchmarks and the destructive reset.

---

# Reference & troubleshooting

## Service URLs and logs

| Service | URL | Log |
|---|---|---|
| HDFS NameNode | http://localhost:9870 | `~/stack-logs/hdfs.log` |
| Hive Metastore | thrift://localhost:9083 | `~/stack-logs/hive-metastore.log` |
| Kafka | localhost:9092 | `~/stack-logs/kafka.log` |
| SeaTunnel Zeta | http://localhost:5801 | `~/stack-logs/seatunnel.log` |
| Druid | http://localhost:8888 | `~/stack-logs/druid.log` |
| Airflow | http://localhost:8080 | `~/stack-logs/airflow.log` |
| Dashboard / API | :5173 / :8000 | `~/stack-logs/ui-*.log` |

## Verify bronze, silver and Iceberg

Bronze rows:

```bash
spark-sql "${SPARK_ICEBERG[@]}" \
  -e "SELECT COUNT(*) FROM bronze.market_quotes; SELECT * FROM bronze.market_quotes LIMIT 5;"
```

Silver counts + warm-up sanity — expect one row per `(symbol, trade_date)`, and
null counts roughly symbols x warm-up (`sma_200` ≈ symbols x 199,
`rsi_14` ≈ symbols x 14):

```bash
spark-sql "${SPARK_ICEBERG[@]}" \
  -e "SELECT COUNT(*) AS rows, COUNT(DISTINCT symbol) AS symbols, SUM(CASE WHEN sma_200 IS NULL THEN 1 ELSE 0 END) AS null_sma200, SUM(CASE WHEN rsi_14 IS NULL THEN 1 ELSE 0 END) AS null_rsi FROM silver.quotes_enriched;"

spark-sql "${SPARK_ICEBERG[@]}" \
  -e "SELECT COUNT(*) AS fund_rows FROM silver.fundamentals_clean; SELECT symbol, trade_date, close, sma_20, sma_50, sma_200, ema_12, rsi_14, macd, volatility_20d, volume_ratio, price_momentum_1m FROM silver.quotes_enriched WHERE symbol='RELIANCE.NS' ORDER BY trade_date DESC LIMIT 5;"
```

Cross-check one symbol against an independent yfinance + pandas recompute (values
should match to ~4 decimals; small differences are because bronze stores prices as
`DECIMAL(18,2)`):

```bash
cd poller && .venv/bin/python -c "
import sys; sys.path.insert(0, '../spark/jobs')
import yfinance as yf, pandas as pd
from indicators import compute_features

df = yf.Ticker('RELIANCE.NS').history(period='2y', interval='1d', auto_adjust=True).reset_index()
pdf = df.rename(columns={'Date': 'trade_date', 'Close': 'close', 'Volume': 'volume'})
pdf['symbol'] = 'RELIANCE.NS'
out = compute_features(pdf[['symbol', 'trade_date', 'close', 'volume']])
out['d'] = pd.to_datetime(out['trade_date']).dt.strftime('%Y-%m-%d')
r = out.iloc[-1]
print('close', round(r['close'], 2), 'sma_20', round(r['sma_20'], 4),
      'sma_50', round(r['sma_50'], 4), 'sma_200', round(r['sma_200'], 4))
print('ema_12', round(r['ema_12'], 4), 'rsi_14', round(r['rsi_14'], 4),
      'macd', round(r['macd'], 4), 'vol_ratio', round(r['volume_ratio'], 4))
"
```

## Dead-letter inspection

```bash
$KAFKA_HOME/bin/kafka-console-consumer.sh \
  --bootstrap-server localhost:9092 \
  --topic market.deadletter --from-beginning --max-messages 10
```

Each message carries `source_topic`, `reason` and `failed_at` (e.g.
`reason: non_positive_close`).

## Benchmarks

Reproducible experiments under `benchmarks/` (each records the git commit and
config, and writes JSON + a markdown summary). See `docs/project-status.md` for
which were run.

```bash
python benchmarks/throughput.py --sizes 50 200 500        # exp 1
python benchmarks/latency.py --repeat 3                   # exp 2
python benchmarks/model.py --run                          # exp 3
python benchmarks/recovery.py --component kafka \
    --kill-cmd "..." --start-cmd "..."                    # exp 4
spark-submit --packages org.apache.iceberg:iceberg-spark-runtime-3.3_2.12:1.8.1 \
    benchmarks/iceberg.py                                 # exp 5
python benchmarks/scalability.py --masters "local[4]" "local[8]" --limit 100   # exp 6
```

## Iceberg maintenance

Compacts small files, rewrites manifests and expires snapshots older than 7 days
(configurable). Trigger from the dashboard **Maintenance** action, the Airflow DAG
`screening_maintenance`, or directly:

```bash
scripts/maintenance.sh
SNAPSHOT_RETENTION_DAYS=30 scripts/maintenance.sh
DRY_RUN=1 scripts/maintenance.sh
```

See `iceberg/maintenance/README.md` for the procedure reference and time-travel /
schema-evolution example queries.

## Unit tests

```bash
python tests/test_indicators.py
python tests/test_features.py
python tests/test_benchmarks.py
python tests/test_monitoring.py
python tests/test_data_quality.py
python tests/test_supervisors.py

# End-to-end smoke test (opt-in; requires the stack + UI up)
RUN_E2E=1 python tests/test_pipeline_e2e.py --limit 10
```

## Full reset (destructive)

Starts over. Clears Kafka topics and the Iceberg warehouse, recreates the tables,
then redo **Section 1** (data load through training). Stop any running SeaTunnel
jobs first (`for p in $(pgrep -f "SeaTunnel[C]lient"); do kill "$p"; done`).

```bash
# 1. delete and recreate Kafka topics, and reset the SeaTunnel consumer groups
for t in market.quotes market.quotes.daily market.fundamentals market.scores market.screener market.history market.deadletter; do
  $KAFKA_HOME/bin/kafka-topics.sh --delete --topic "$t" --bootstrap-server localhost:9092
done
sleep 5
./kafka/topics/create-topics.sh

# group_offsets means SeaTunnel resumes from committed offsets; clear them so a
# fresh topic is re-read from the start.
for g in seatunnel-bronze-quotes seatunnel-bronze-quotes-daily seatunnel-bronze-fundamentals; do
  $KAFKA_HOME/bin/kafka-consumer-groups.sh --delete --group "$g" --bootstrap-server localhost:9092
done

# 2. drop Iceberg tables and remove their warehouse data
spark-sql "${SPARK_ICEBERG[@]}" -e "DROP TABLE IF EXISTS bronze.market_quotes; DROP TABLE IF EXISTS bronze.quotes_daily; DROP TABLE IF EXISTS bronze.market_fundamentals; DROP TABLE IF EXISTS silver.quotes_enriched; DROP TABLE IF EXISTS silver.fundamentals_clean; DROP TABLE IF EXISTS ml.training_dataset; DROP TABLE IF EXISTS gold.stock_scores; DROP TABLE IF EXISTS gold.top_picks;"

hdfs dfs -rm -r -f /warehouse/bronze/market_quotes /warehouse/bronze/quotes_daily /warehouse/bronze/market_fundamentals /warehouse/silver /warehouse/ml /warehouse/gold

# 3. recreate the Iceberg tables
spark-sql "${SPARK_ICEBERG[@]}" -f iceberg/schemas/bronze-schema.sql
spark-sql "${SPARK_ICEBERG[@]}" -f iceberg/schemas/silver-schema.sql
spark-sql "${SPARK_ICEBERG[@]}" -f iceberg/schemas/gold-schema.sql
```

Then repeat **Section 1**, starting at 1.6 (Load historical data).
