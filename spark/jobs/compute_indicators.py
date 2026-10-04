"""
compute_indicators.py — Spark batch job: bronze.quotes_daily -> silver.quotes_enriched.

Reads historical daily bars, computes the per-symbol technical indicators from
docs/project-spec.md §6.3, and overwrites the silver table. Also refreshes
silver.fundamentals_clean with the latest fundamentals snapshot per symbol.

Run (from the repo root):
    spark-submit \
      --packages org.apache.iceberg:iceberg-spark-runtime-3.3_2.12:1.8.1 \
      spark/jobs/compute_indicators.py

Prereqs: `spark-sql ... -f iceberg/schemas/silver-schema.sql` has been run.
"""

import argparse
import os
import sys

from pyspark.sql import SparkSession, Window
from pyspark.sql import functions as F
from pyspark.sql.types import (
    DateType,
    DoubleType,
    LongType,
    StringType,
    StructField,
    StructType,
)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from indicators import compute_features  # noqa: E402
from nullability import match_not_null  # noqa: E402

FEATURE_SCHEMA = StructType(
    [
        StructField("symbol", StringType()),
        StructField("trade_date", DateType()),
        StructField("close", DoubleType()),
        StructField("volume", LongType()),
        StructField("sma_20", DoubleType()),
        StructField("sma_50", DoubleType()),
        StructField("sma_200", DoubleType()),
        StructField("ema_12", DoubleType()),
        StructField("ema_26", DoubleType()),
        StructField("rsi_14", DoubleType()),
        StructField("macd", DoubleType()),
        StructField("macd_signal", DoubleType()),
        StructField("volatility_20d", DoubleType()),
        StructField("volume_avg_20d", DoubleType()),
        StructField("volume_ratio", DoubleType()),
        StructField("distance_from_52w_high", DoubleType()),
        StructField("distance_from_52w_low", DoubleType()),
        StructField("price_momentum_1m", DoubleType()),
        StructField("price_momentum_3m", DoubleType()),
        StructField("price_momentum_6m", DoubleType()),
    ]
)

ENRICHED_COLUMNS = [
    "symbol", "trade_date", "close", "volume",
    "sma_20", "sma_50", "sma_200", "ema_12", "ema_26", "rsi_14",
    "macd", "macd_signal", "volatility_20d", "volume_avg_20d", "volume_ratio",
    "distance_from_52w_high", "distance_from_52w_low",
    "price_momentum_1m", "price_momentum_3m", "price_momentum_6m",
    "computed_at",
]


def build_spark(warehouse: str) -> SparkSession:
    spark = (
        SparkSession.builder.appName("compute_indicators")
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
    # Ship the pure-pandas feature module to the executors so the
    # applyInPandas worker can import it.
    spark.sparkContext.addPyFile(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "indicators.py")
    )
    return spark


def compute_quotes_enriched(spark: SparkSession) -> int:
    daily = (
        spark.table("iceberg.bronze.quotes_daily")
        .select(
            "symbol",
            "trade_date",
            F.col("close").cast("double").alias("close"),
            "volume",
        )
        # Backfills are re-runnable, so bronze can legitimately contain duplicate
        # (symbol, trade_date) rows; dedupe before rolling windows.
        .dropDuplicates(["symbol", "trade_date"])
    )

    features = (
        daily.groupBy("symbol")
        .applyInPandas(compute_features, schema=FEATURE_SCHEMA)
        .withColumn("computed_at", F.current_timestamp())
        .select(*ENRICHED_COLUMNS)
    )
    features = match_not_null(features, spark, "iceberg.silver.quotes_enriched")
    features.createOrReplaceTempView("quotes_enriched_stage")

    spark.sql(
        """
        INSERT OVERWRITE iceberg.silver.quotes_enriched
        SELECT * FROM quotes_enriched_stage
        """
    )

    return spark.table("iceberg.silver.quotes_enriched").count()


def refresh_fundamentals_clean(spark: SparkSession) -> int:
    fund = spark.table("iceberg.bronze.market_fundamentals")
    if fund.rdd.isEmpty():
        print("No fundamentals yet — skipping silver.fundamentals_clean.")
        return 0

    latest_window = Window.partitionBy("symbol").orderBy(F.col("timestamp").desc())
    latest = (
        fund.withColumn("rn", F.row_number().over(latest_window))
        .filter(F.col("rn") == 1)
        .select(
            "symbol",
            F.col("timestamp").alias("updated_at"),
            "pe_ratio", "pb_ratio", "eps", "dividend_yield", "market_cap", "beta",
            "fifty_two_week_high", "fifty_two_week_low", "return_on_equity",
            "debt_to_equity", "revenue_growth", "earnings_growth",
        )
    )
    latest = match_not_null(latest, spark, "iceberg.silver.fundamentals_clean")
    latest.createOrReplaceTempView("fundamentals_clean_stage")
    spark.sql(
        """
        INSERT OVERWRITE iceberg.silver.fundamentals_clean
        SELECT * FROM fundamentals_clean_stage
        """
    )
    return spark.table("iceberg.silver.fundamentals_clean").count()


def main():
    parser = argparse.ArgumentParser(description="Compute silver technical indicators")
    parser.add_argument(
        "--warehouse", default="hdfs://localhost:9000/warehouse",
        help="Iceberg warehouse URI",
    )
    parser.add_argument(
        "--skip-fundamentals", action="store_true",
        help="Only refresh quotes_enriched",
    )
    args = parser.parse_args()

    spark = build_spark(args.warehouse)
    spark.sparkContext.setLogLevel("WARN")

    n_quotes = compute_quotes_enriched(spark)
    print(f"silver.quotes_enriched rows: {n_quotes}")

    if not args.skip_fundamentals:
        n_fund = refresh_fundamentals_clean(spark)
        print(f"silver.fundamentals_clean rows: {n_fund}")

    spark.stop()


if __name__ == "__main__":
    main()
