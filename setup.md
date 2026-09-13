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

Each command runs a streaming job (blocks until Ctrl-C). Run them in separate
terminals, or detach with `setsid ... &`.

```bash
# Kafka -> Iceberg bronze (quotes)
$SEATUNNEL_HOME/bin/seatunnel.sh --config seatunnel/configs/quotes-job.conf

# Kafka -> Iceberg bronze (daily bars)
$SEATUNNEL_HOME/bin/seatunnel.sh --config seatunnel/configs/quotes-daily-job.conf

# Kafka -> Iceberg bronze (fundamentals)
$SEATUNNEL_HOME/bin/seatunnel.sh --config seatunnel/configs/fundamentals-job.conf
```

Commits happen on each checkpoint (every 10s). Watch the engine log:
`~/seatunnel/logs/seatunnel-engine-server.log` for `do commit table`.

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
