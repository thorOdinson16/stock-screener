### Convert start, stop, and topic scripts into executable

```bash
chmod +x start-stack.sh stop-stack.sh kafka/topics/create-topics.sh
```

### Start the Stack
```bash
./start-stack.sh
```

Starts HDFS -> Hive Metastore -> Kafka -> SeaTunnel -> Druid -> Airflow.
Logs: `~/stack-logs/`. Verify with `jps` and `ss -tln`.

### Create Kafka Topics (after the stack is up)
```bash
./kafka/topics/create-topics.sh
```

Creates `market.quotes`, `market.quotes.daily`, `market.fundamentals`, `market.scores`,
`market.deadletter`.

### Setup for Poller
```bash
cd poller
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# small test first — don't hit all 500 symbols on the first run
python poller.py --once --universe-limit 50
# or the full universe (takes several minutes)
python poller.py --once
```

Publishes one snapshot per symbol to `market.quotes` and `market.fundamentals`.
Re-run with `--once --quotes-only` to publish quotes only.

### Historical backfill (daily bars)

The live poller only captures the current snapshot, so load ~2 years of daily bars
once. This is what makes `sma_200`, momentum and 52-week features meaningful.

```bash
cd poller && source .venv/bin/activate
python backfill.py --once --universe-limit 20   # quick test
python backfill.py --once                        # full universe (~4-5 min)
```

Publishes to `market.quotes.daily`. Backfills are re-runnable; the indicator job
dedupes `(symbol, trade_date)`.

### Verify Kafka
```bash
$KAFKA_HOME/bin/kafka-console-consumer.sh \
  --bootstrap-server localhost:9092 \
  --topic market.quotes --from-beginning --max-messages 5
```

Timestamps are emitted as UTC ISO-8601 with a `Z` suffix (e.g.
`2026-09-12T12:27:35.682456Z`).

### Create Hadoop Catalog (Iceberg namespace)
```bash
spark-sql \
  --packages org.apache.iceberg:iceberg-spark-runtime-4.1_2.13:1.11.0 \
  --conf spark.sql.catalog.iceberg=org.apache.iceberg.spark.SparkCatalog \
  --conf spark.sql.catalog.iceberg.type=hadoop \
  --conf spark.sql.catalog.iceberg.warehouse=hdfs://localhost:9000/warehouse \
  --conf spark.sql.defaultCatalog=iceberg \
  -e "CREATE NAMESPACE IF NOT EXISTS bronze;"
```

### Run Bronze Schema file
```bash
spark-sql \
  --packages org.apache.iceberg:iceberg-spark-runtime-4.1_2.13:1.11.0 \
  --conf spark.sql.catalog.iceberg=org.apache.iceberg.spark.SparkCatalog \
  --conf spark.sql.catalog.iceberg.type=hadoop \
  --conf spark.sql.catalog.iceberg.warehouse=hdfs://localhost:9000/warehouse \
  --conf spark.sql.defaultCatalog=iceberg \
  -f iceberg/schemas/bronze-schema.sql
```

### Run Silver Schema file
```bash
spark-sql \
  --packages org.apache.iceberg:iceberg-spark-runtime-4.1_2.13:1.11.0 \
  --conf spark.sql.catalog.iceberg=org.apache.iceberg.spark.SparkCatalog \
  --conf spark.sql.catalog.iceberg.type=hadoop \
  --conf spark.sql.catalog.iceberg.warehouse=hdfs://localhost:9000/warehouse \
  --conf spark.sql.defaultCatalog=iceberg \
  -f iceberg/schemas/silver-schema.sql
```

Creates `silver.quotes_enriched` and `silver.fundamentals_clean`.

### Verify Iceberg tables
```bash
spark-sql \
  --packages org.apache.iceberg:iceberg-spark-runtime-4.1_2.13:1.11.0 \
  --conf spark.sql.catalog.iceberg=org.apache.iceberg.spark.SparkCatalog \
  --conf spark.sql.catalog.iceberg.type=hadoop \
  --conf spark.sql.catalog.iceberg.warehouse=hdfs://localhost:9000/warehouse \
  --conf spark.sql.defaultCatalog=iceberg \
  -e "SHOW TABLES IN bronze; SHOW TABLES IN silver;"
```

### Run SeaTunnel ingestion jobs

These are **BATCH** jobs: each drains its Kafka topic (everything available at
start), commits to Iceberg, then terminates on its own. Re-running re-reads from
`earliest`, so clear the topics/tables first to avoid duplicates.

```bash
# Kafka -> Iceberg bronze (quotes)
$SEATUNNEL_HOME/bin/seatunnel.sh --config seatunnel/configs/quotes-job.conf

# Kafka -> Iceberg bronze (daily bars)
$SEATUNNEL_HOME/bin/seatunnel.sh --config seatunnel/configs/quotes-daily-job.conf

# Kafka -> Iceberg bronze (fundamentals)
$SEATUNNEL_HOME/bin/seatunnel.sh --config seatunnel/configs/fundamentals-job.conf
```

Each job prints its result and exits. Commits are visible in the engine log:
`~/seatunnel/logs/seatunnel-engine-server.log` (`do commit table`).

### Verify ingested data
```bash
spark-sql \
  --packages org.apache.iceberg:iceberg-spark-runtime-4.1_2.13:1.11.0 \
  --conf spark.sql.catalog.iceberg=org.apache.iceberg.spark.SparkCatalog \
  --conf spark.sql.catalog.iceberg.type=hadoop \
  --conf spark.sql.catalog.iceberg.warehouse=hdfs://localhost:9000/warehouse \
  --conf spark.sql.defaultCatalog=iceberg \
  --conf spark.sql.session.timeZone=UTC \
  -e "SELECT COUNT(*) FROM bronze.market_quotes; SELECT * FROM bronze.market_quotes LIMIT 5;"
```

### Compute silver indicators (Spark batch)

Reads `bronze.quotes_daily`, computes the technical indicators from
`docs/project-spec.md` §6.3, and overwrites `silver.quotes_enriched` +
`silver.fundamentals_clean`.

```bash
spark-submit \
  --packages org.apache.iceberg:iceberg-spark-runtime-4.1_2.13:1.11.0 \
  spark/jobs/compute_indicators.py
```

Unit-test the indicator math (no Spark needed):

```bash
python tests/test_indicators.py
```

### Verify silver indicators

Counts + warm-up sanity — expect one row per `(symbol, trade_date)`, and null
counts equal to symbols x warm-up (`sma_200` ≈ symbols x 199, `rsi_14` ≈ symbols x 14):

```bash
spark-sql \
  --packages org.apache.iceberg:iceberg-spark-runtime-4.1_2.13:1.11.0 \
  --conf spark.sql.catalog.iceberg=org.apache.iceberg.spark.SparkCatalog \
  --conf spark.sql.catalog.iceberg.type=hadoop \
  --conf spark.sql.catalog.iceberg.warehouse=hdfs://localhost:9000/warehouse \
  --conf spark.sql.defaultCatalog=iceberg \
  --conf spark.sql.session.timeZone=UTC \
  -e "SELECT COUNT(*) AS rows, COUNT(DISTINCT symbol) AS symbols, SUM(CASE WHEN sma_200 IS NULL THEN 1 ELSE 0 END) AS null_sma200, SUM(CASE WHEN rsi_14 IS NULL THEN 1 ELSE 0 END) AS null_rsi FROM silver.quotes_enriched;"
```

Sample values + fundamentals count:

```bash
spark-sql \
  --packages org.apache.iceberg:iceberg-spark-runtime-4.1_2.13:1.11.0 \
  --conf spark.sql.catalog.iceberg=org.apache.iceberg.spark.SparkCatalog \
  --conf spark.sql.catalog.iceberg.type=hadoop \
  --conf spark.sql.catalog.iceberg.warehouse=hdfs://localhost:9000/warehouse \
  --conf spark.sql.defaultCatalog=iceberg \
  --conf spark.sql.session.timeZone=UTC \
  -e "SELECT COUNT(*) AS fund_rows FROM silver.fundamentals_clean; SELECT symbol, trade_date, close, sma_20, sma_50, sma_200, ema_12, rsi_14, macd, volatility_20d, volume_ratio, price_momentum_1m FROM silver.quotes_enriched WHERE symbol='RELIANCE.NS' ORDER BY trade_date DESC LIMIT 5;"
```

Cross-check one symbol against an independent yfinance + pandas recompute. Values
should match to ~4 decimal places; small differences are only because bronze stores
prices as `DECIMAL(18,2)`:

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
r = out[out['d'] == '2026-09-11'].iloc[0]
print('close', round(r['close'], 2), 'sma_20', round(r['sma_20'], 4),
      'sma_50', round(r['sma_50'], 4), 'sma_200', round(r['sma_200'], 4))
print('ema_12', round(r['ema_12'], 4), 'rsi_14', round(r['rsi_14'], 4),
      'macd', round(r['macd'], 4), 'vol_ratio', round(r['volume_ratio'], 4))
"
```

### Backfill the benchmark index (optional but needed for index-relative metrics)

The evaluation layer benchmarks top-K picks against the cross-sectional universe
mean **and** the NIFTY index. The index rides the same daily pipeline but is
excluded from the tradable universe, training and scoring:

```bash
cd poller && source .venv/bin/activate
python backfill.py --once --include-index --period 2y
```

This publishes `^NSEI` daily bars to `market.quotes.daily`; re-run the
`quotes-daily` SeaTunnel job (and the indicator job) so the index reaches silver.

If the NIFTY 500 daily bars are already ingested, add the index without
re-publishing the whole universe: clear the topic, publish only the index, ingest,
then recompute indicators:

```bash
$KAFKA_HOME/bin/kafka-topics.sh --delete --topic market.quotes.daily --bootstrap-server localhost:9092
sleep 5 && ./kafka/topics/create-topics.sh
python backfill.py --once --index-only --period 2y
$SEATUNNEL_HOME/bin/seatunnel.sh --config seatunnel/configs/quotes-daily-job.conf
```

### Create gold + ML tables

```bash
spark-sql \
  --packages org.apache.iceberg:iceberg-spark-runtime-4.1_2.13:1.11.0 \
  --conf spark.sql.catalog.iceberg=org.apache.iceberg.spark.SparkCatalog \
  --conf spark.sql.catalog.iceberg.type=hadoop \
  --conf spark.sql.catalog.iceberg.warehouse=hdfs://localhost:9000/warehouse \
  --conf spark.sql.defaultCatalog=iceberg \
  -f iceberg/schemas/gold-schema.sql
```

Creates `gold.stock_scores`, `gold.top_picks`, `ml.training_dataset`.

### Unit-test the feature/label/metric math

```bash
python tests/test_features.py
```

### Build the training dataset

Adds both forward-return labels, the two benchmark returns, and the
leakage-safe train/embargo/test split:

```bash
spark-submit \
  --driver-memory 4g --master "local[8]" \
  --packages org.apache.iceberg:iceberg-spark-runtime-4.1_2.13:1.11.0 \
  spark/jobs/build_training.py
```

Expect roughly one row per `(symbol, trade_date)` for non-index symbols; the last
5/21 bars per symbol have null labels and the split is `train`/`embargo`/`test`.

### Train models

Trains GBT, RandomForest and LinearRegression for each label (`fwd_ret_5d`,
`fwd_ret_21d`) on the `train` split only. Tree training needs a larger driver heap
and bounded task parallelism when running in local mode, hence the flags below:

```bash
spark-submit \
  --driver-memory 6g --master "local[8]" \
  --packages org.apache.iceberg:iceberg-spark-runtime-4.1_2.13:1.11.0 \
  ml/training/train_model.py
```

Artifacts go to `ml/models/<algo>_<label>_<timestamp>/`, indexed by
`ml/models/registry.json`.

### Evaluate + select the best model per label

Scores every model and the interpretable rule baseline (spec §13) on the `test`
split, reporting Information Coefficient, Precision@K (vs both benchmarks),
RMSE/MAE, mean top-K forward return and turnover:

```bash
spark-submit \
  --driver-memory 4g --master "local[8]" \
  --packages org.apache.iceberg:iceberg-spark-runtime-4.1_2.13:1.11.0 \
  ml/evaluation/evaluate.py
```

Writes `ml/evaluation/results/comparison.{json,md}` and
`ml/models/selected.json`.

### Score the universe -> gold + Kafka

```bash
spark-submit \
  --driver-memory 4g --master "local[8]" \
  --packages org.apache.iceberg:iceberg-spark-runtime-4.1_2.13:1.11.0,org.apache.spark:spark-sql-kafka-0-10_2.13:4.1.3 \
  spark/jobs/score_stocks.py
```

Ranks the latest cross-section, writes `gold.stock_scores` + `gold.top_picks` for
the latest trade date (idempotent — re-runs replace that date), and publishes to
`market.scores`. If the Kafka connector isn't on the classpath the gold writes
still succeed and publishing is skipped with a warning. Verify:

```bash
spark-sql \
  --packages org.apache.iceberg:iceberg-spark-runtime-4.1_2.13:1.11.0 \
  --conf spark.sql.catalog.iceberg=org.apache.iceberg.spark.SparkCatalog \
  --conf spark.sql.catalog.iceberg.type=hadoop \
  --conf spark.sql.catalog.iceberg.warehouse=hdfs://localhost:9000/warehouse \
  --conf spark.sql.defaultCatalog=iceberg \
  -e "SELECT * FROM gold.top_picks ORDER BY label, rank LIMIT 20;"
```

### Start the Druid serving layer

Requires Druid running (started by `start-stack.sh`). Registers the Kafka
ingestion supervisors, then publishes a latest snapshot and the price history:

```bash
# 1. create the serving topics (idempotent; includes market.screener/history)
./kafka/topics/create-topics.sh

# 2. register the Druid Kafka supervisors
./druid/ingestion/submit.sh

# 3. publish the latest per-symbol snapshot -> market.screener
spark-submit \
  --driver-memory 4g --master "local[8]" \
  --packages org.apache.iceberg:iceberg-spark-runtime-4.1_2.13:1.11.0,org.apache.spark:spark-sql-kafka-0-10_2.13:4.1.3 \
  spark/jobs/publish_screener.py

# 4. publish daily indicator bars -> market.history (~240k rows)
spark-submit \
  --driver-memory 4g --master "local[8]" \
  --packages org.apache.iceberg:iceberg-spark-runtime-4.1_2.13:1.11.0,org.apache.spark:spark-sql-kafka-0-10_2.13:4.1.3 \
  spark/jobs/publish_history.py
```

Druid hands off segments within ~1-2 minutes. Verify:

```bash
curl -s http://localhost:8888/druid/coordinator/v1/datasources
# -> ["price_history","screener","stock_scores"]  (market_quotes joins after the poller runs)
```

`market_quotes` ingests the `market.quotes` topic, which has 1-day Kafka retention —
run `poller/poller.py --once` to repopulate it. The dashboard does not depend on it.

### Web dashboard

```bash
./start-ui.sh
```

- Dashboard  http://localhost:5173
- API docs   http://localhost:8000/docs

Pages: market overview, top picks, screener, stock detail, model evaluation.
Manual refresh by default; toggle 30s auto-refresh in the sidebar. Stop with
`./stop-ui.sh`.

### On-demand pipeline (button / Airflow)

The Dashboard header's **Run pipeline** button (Quick or Full, with an optional
universe limit and price-history publish) and the **Retrain model** action trigger
Airflow DAGs, which run the `scripts/` wrappers step by step.

Configure the Airflow REST credentials once:

```bash
cp config/airflow.env.example config/airflow.env
# fill AIRFLOW_PASSWORD from ~/airflow/simple_auth_manager_passwords.json.generated
```

`start-stack.sh` points Airflow's `dags_folder` at this repo's `airflow/dags/`
and creates the `screening` pool (1 slot) so runs never overlap. The DAGs
(`screening_on_demand`, `screening_retrain`) can also be triggered from the
Airflow UI at http://localhost:8080.

Run a pipeline without the UI (debugging):

```bash
scripts/run_once.sh --full --history       # or: scripts/run_once.sh --limit 20
```

### Reset and run once again

Clears all Kafka topics and empties the Iceberg bronze + silver tables. Stop any running
SeaTunnel jobs first (Ctrl-C, or `for p in $(pgrep -f "SeaTunnel[C]lient"); do kill "$p"; done`).

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
spark-sql \
  --packages org.apache.iceberg:iceberg-spark-runtime-4.1_2.13:1.11.0 \
  --conf spark.sql.catalog.iceberg=org.apache.iceberg.spark.SparkCatalog \
  --conf spark.sql.catalog.iceberg.type=hadoop \
  --conf spark.sql.catalog.iceberg.warehouse=hdfs://localhost:9000/warehouse \
  --conf spark.sql.defaultCatalog=iceberg \
  -e "DROP TABLE IF EXISTS bronze.market_quotes; DROP TABLE IF EXISTS bronze.quotes_daily; DROP TABLE IF EXISTS bronze.market_fundamentals; DROP TABLE IF EXISTS silver.quotes_enriched; DROP TABLE IF EXISTS silver.fundamentals_clean; DROP TABLE IF EXISTS ml.training_dataset; DROP TABLE IF EXISTS gold.stock_scores; DROP TABLE IF EXISTS gold.top_picks;"

hdfs dfs -rm -r -f /warehouse/bronze/market_quotes /warehouse/bronze/quotes_daily /warehouse/bronze/market_fundamentals /warehouse/silver /warehouse/ml /warehouse/gold

# 3. recreate the bronze + silver tables
spark-sql \
  --packages org.apache.iceberg:iceberg-spark-runtime-4.1_2.13:1.11.0 \
  --conf spark.sql.catalog.iceberg=org.apache.iceberg.spark.SparkCatalog \
  --conf spark.sql.catalog.iceberg.type=hadoop \
  --conf spark.sql.catalog.iceberg.warehouse=hdfs://localhost:9000/warehouse \
  --conf spark.sql.defaultCatalog=iceberg \
  -f iceberg/schemas/bronze-schema.sql

spark-sql \
  --packages org.apache.iceberg:iceberg-spark-runtime-4.1_2.13:1.11.0 \
  --conf spark.sql.catalog.iceberg=org.apache.iceberg.spark.SparkCatalog \
  --conf spark.sql.catalog.iceberg.type=hadoop \
  --conf spark.sql.catalog.iceberg.warehouse=hdfs://localhost:9000/warehouse \
  --conf spark.sql.defaultCatalog=iceberg \
  -f iceberg/schemas/silver-schema.sql

spark-sql \
  --packages org.apache.iceberg:iceberg-spark-runtime-4.1_2.13:1.11.0 \
  --conf spark.sql.catalog.iceberg=org.apache.iceberg.spark.SparkCatalog \
  --conf spark.sql.catalog.iceberg.type=hadoop \
  --conf spark.sql.catalog.iceberg.warehouse=hdfs://localhost:9000/warehouse \
  --conf spark.sql.defaultCatalog=iceberg \
  -f iceberg/schemas/gold-schema.sql
```

Then repeat the Poller / Backfill -> SeaTunnel -> Verify -> Compute silver
indicators -> Build training dataset -> Train -> Evaluate -> Score steps above.
