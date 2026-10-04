"""
publish_history.py — publishes daily indicator bars to the `market.history` Kafka
topic for Druid ingestion (dashboard price/indicator charts).

Source: silver.quotes_enriched. Benchmark index symbols are excluded so the charts
cover the tradable universe only. On-demand runs publish incrementally via
`--since` (bars newer than what Druid already serves).

Run (from the repo root):
    spark-submit \
      --packages org.apache.iceberg:iceberg-spark-runtime-3.3_2.12:1.8.1,org.apache.spark:spark-sql-kafka-0-10_2.12:3.3.4 \
      spark/jobs/publish_history.py
"""

import argparse
import os
import sys

from pyspark.sql import SparkSession
from pyspark.sql import functions as F

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
sys.path.insert(0, os.path.join(_REPO_ROOT, "ml", "feature_engineering"))
from features import BENCHMARK_SYMBOLS  # noqa: E402

HISTORY_TOPIC = "market.history"

HISTORY_COLUMNS = [
    "symbol", "trade_date", "close", "volume",
    "sma_20", "sma_50", "sma_200", "ema_12", "ema_26", "rsi_14",
    "macd", "macd_signal", "volatility_20d", "volume_ratio",
]


def build_spark(warehouse: str) -> SparkSession:
    return (
        SparkSession.builder.appName("publish_history")
        .config(
            "spark.sql.extensions",
            "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions",
        )
        .config("spark.sql.catalog.iceberg", "org.apache.iceberg.spark.SparkCatalog")
        .config("spark.sql.catalog.iceberg.type", "hadoop")
        .config("spark.sql.catalog.iceberg.warehouse", warehouse)
        .config("spark.sql.defaultCatalog", "iceberg")
        .config("spark.sql.session.timeZone", "UTC")
        .getOrCreate()
    )


def main():
    parser = argparse.ArgumentParser(description="Publish daily indicator bars to Kafka")
    parser.add_argument("--warehouse", default="hdfs://localhost:9000/warehouse")
    parser.add_argument("--bootstrap-servers", default="localhost:9092")
    parser.add_argument("--topic", default=HISTORY_TOPIC)
    parser.add_argument(
        "--since", default=None,
        help="Only publish bars with trade_date > this date (incremental, on-demand)",
    )
    args = parser.parse_args()

    spark = build_spark(args.warehouse)
    spark.sparkContext.setLogLevel("WARN")

    source = spark.table("iceberg.silver.quotes_enriched").filter(
        ~F.col("symbol").isin(*BENCHMARK_SYMBOLS)
    )
    if args.since:
        source = source.filter(F.col("trade_date") > F.lit(args.since).cast("date"))

    history = (
        source
        .select(
            "symbol",
            F.col("trade_date").cast("string").alias("trade_date"),
            F.col("close").cast("double").alias("close"),
            F.col("volume").cast("long").alias("volume"),
            *[F.col(c).cast("double").alias(c) for c in HISTORY_COLUMNS[4:]],
        )
    )

    payload = history.select(
        F.col("symbol").alias("key"),
        F.to_json(F.struct(*HISTORY_COLUMNS)).alias("value"),
    )
    rows = payload.count()
    (
        payload.write.format("kafka")
        .option("kafka.bootstrap.servers", args.bootstrap_servers)
        .option("topic", args.topic)
        .save()
    )
    print(f"Published {rows} rows to {args.topic}.")

    spark.stop()


if __name__ == "__main__":
    main()
