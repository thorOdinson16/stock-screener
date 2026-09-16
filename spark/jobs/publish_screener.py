"""
publish_screener.py — publishes the latest per-symbol snapshot to the
`market.screener` Kafka topic for Druid ingestion (dashboard screener/detail pages).

Joins, per symbol:
  - silver.quotes_enriched  (latest technical indicators)
  - silver.fundamentals_clean (latest fundamentals)
  - gold.stock_scores        (latest score/rank for each label)
  - poller/universe/universe_cache.json (company name + industry metadata)

Run (from the repo root):
    spark-submit \
      --packages org.apache.iceberg:iceberg-spark-runtime-4.1_2.13:1.11.0,org.apache.spark:spark-sql-kafka-0-10_2.13:4.1.3 \
      spark/jobs/publish_screener.py
"""

import argparse
import json
import os
import sys

from pyspark.sql import SparkSession, Window
from pyspark.sql import functions as F

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
sys.path.insert(0, os.path.join(_REPO_ROOT, "ml", "feature_engineering"))
from features import BENCHMARK_SYMBOLS  # noqa: E402

SCREENER_TOPIC = "market.screener"
UNIVERSE_CACHE = os.path.join(_REPO_ROOT, "poller", "universe", "universe_cache.json")

INDICATOR_COLUMNS = [
    "sma_20", "sma_50", "sma_200", "ema_12", "ema_26", "rsi_14", "macd", "macd_signal",
    "volatility_20d", "volume_ratio", "distance_from_52w_high", "distance_from_52w_low",
    "price_momentum_1m", "price_momentum_3m", "price_momentum_6m",
]

FUNDAMENTAL_COLUMNS = [
    "pe_ratio", "pb_ratio", "eps", "dividend_yield", "market_cap", "beta",
    "fifty_two_week_high", "fifty_two_week_low", "return_on_equity", "debt_to_equity",
    "revenue_growth", "earnings_growth",
]

PAYLOAD_COLUMNS = (
    ["symbol", "snapshot_at", "trade_date", "company_name", "industry", "close", "change_percent", "volume"]
    + INDICATOR_COLUMNS
    + FUNDAMENTAL_COLUMNS
    + ["score_5d", "rank_5d", "score_21d", "rank_21d"]
)


def build_spark(warehouse: str) -> SparkSession:
    return (
        SparkSession.builder.appName("publish_screener")
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


def latest_indicators(spark: SparkSession):
    silver = spark.table("iceberg.silver.quotes_enriched").filter(
        ~F.col("symbol").isin(*BENCHMARK_SYMBOLS)
    )
    window = Window.partitionBy("symbol").orderBy(F.col("trade_date").desc())
    return (
        silver.withColumn("rn", F.row_number().over(window))
        .filter(F.col("rn") == 1)
        .select(
            "symbol",
            F.col("trade_date").cast("string").alias("trade_date"),
            F.col("close").cast("double").alias("close"),
            F.col("volume").cast("long").alias("volume"),
            *[F.col(c).cast("double").alias(c) for c in INDICATOR_COLUMNS],
        )
    )


def latest_fundamentals(spark: SparkSession):
    return spark.table("iceberg.silver.fundamentals_clean").select(
        "symbol", *[F.col(c).cast("double").alias(c) for c in FUNDAMENTAL_COLUMNS]
    )


def latest_change_percent(spark: SparkSession):
    """Most recent polled quote per symbol (bronze.market_quotes snapshot)."""
    quotes = spark.table("iceberg.bronze.market_quotes")
    window = Window.partitionBy("symbol").orderBy(F.col("timestamp").desc())
    return (
        quotes.withColumn("rn", F.row_number().over(window))
        .filter(F.col("rn") == 1)
        .select("symbol", F.col("change_percent").cast("double").alias("change_percent"))
    )


def latest_score(spark: SparkSession, label: str, score_alias: str, rank_alias: str):
    scores = spark.table("iceberg.gold.stock_scores").filter(F.col("label") == label)
    window = Window.partitionBy("symbol").orderBy(F.col("scored_at").desc())
    return (
        scores.withColumn("rn", F.row_number().over(window))
        .filter(F.col("rn") == 1)
        .select(
            "symbol",
            F.col("score").cast("double").alias(score_alias),
            F.col("rank").cast("long").alias(rank_alias),
        )
    )


def universe_metadata(spark: SparkSession):
    with open(UNIVERSE_CACHE) as fh:
        stocks = json.load(fh)["stocks"]
    rows = [(s["yf_symbol"], s["company_name"], s["industry"]) for s in stocks]
    return spark.createDataFrame(rows, ["symbol", "company_name", "industry"])


def publish(df, bootstrap_servers: str) -> None:
    payload = df.select(
        F.col("symbol").alias("key"),
        F.to_json(
            F.struct(
                *[
                    F.col(c).cast("string").alias(c) if c in ("snapshot_at", "trade_date")
                    else F.col(c)
                    for c in PAYLOAD_COLUMNS
                ]
            )
        ).alias("value"),
    )
    (
        payload.write.format("kafka")
        .option("kafka.bootstrap.servers", bootstrap_servers)
        .option("topic", SCREENER_TOPIC)
        .save()
    )


def main():
    parser = argparse.ArgumentParser(description="Publish latest screener snapshot to Kafka")
    parser.add_argument("--warehouse", default="hdfs://localhost:9000/warehouse")
    parser.add_argument("--bootstrap-servers", default="localhost:9092")
    args = parser.parse_args()

    spark = build_spark(args.warehouse)
    spark.sparkContext.setLogLevel("WARN")

    snapshot = (
        latest_indicators(spark)
        .join(latest_change_percent(spark), "symbol", "left")
        .join(latest_fundamentals(spark), "symbol", "left")
        .join(latest_score(spark, "excess_ret_5d", "score_5d", "rank_5d"), "symbol", "left")
        .join(latest_score(spark, "excess_ret_21d", "score_21d", "rank_21d"), "symbol", "left")
        .join(F.broadcast(universe_metadata(spark)), "symbol", "left")
        .withColumn(
            "snapshot_at",
            F.date_format(F.current_timestamp(), "yyyy-MM-dd'T'HH:mm:ss.SSS'Z'"),
        )
        .select(*PAYLOAD_COLUMNS)
    )

    rows = snapshot.count()
    publish(snapshot, args.bootstrap_servers)
    print(f"Published {rows} rows to {SCREENER_TOPIC}.")

    spark.stop()


if __name__ == "__main__":
    main()
