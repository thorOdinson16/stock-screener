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

### Reset and run once again

Clears all Kafka topics and empties the Iceberg bronze + silver tables. Stop any running
SeaTunnel jobs first (Ctrl-C, or `for p in $(pgrep -f "SeaTunnel[C]lient"); do kill "$p"; done`).

```bash
# 1. delete and recreate Kafka topics
for t in market.quotes market.quotes.daily market.fundamentals market.scores market.deadletter; do
  $KAFKA_HOME/bin/kafka-topics.sh --delete --topic "$t" --bootstrap-server localhost:9092
done
sleep 5
./kafka/topics/create-topics.sh

# 2. drop Iceberg tables and remove their warehouse data
spark-sql \
  --packages org.apache.iceberg:iceberg-spark-runtime-4.1_2.13:1.11.0 \
  --conf spark.sql.catalog.iceberg=org.apache.iceberg.spark.SparkCatalog \
  --conf spark.sql.catalog.iceberg.type=hadoop \
  --conf spark.sql.catalog.iceberg.warehouse=hdfs://localhost:9000/warehouse \
  --conf spark.sql.defaultCatalog=iceberg \
  -e "DROP TABLE IF EXISTS bronze.market_quotes; DROP TABLE IF EXISTS bronze.quotes_daily; DROP TABLE IF EXISTS bronze.market_fundamentals; DROP TABLE IF EXISTS silver.quotes_enriched; DROP TABLE IF EXISTS silver.fundamentals_clean;"

hdfs dfs -rm -r -f /warehouse/bronze/market_quotes /warehouse/bronze/quotes_daily /warehouse/bronze/market_fundamentals /warehouse/silver

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
```

Then repeat the Poller / Backfill -> SeaTunnel -> Verify -> Compute silver indicators steps above.
