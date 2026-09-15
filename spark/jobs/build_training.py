"""
build_training.py — silver -> ml.training_dataset.

Reads silver.quotes_enriched, adds both forward-return labels, attaches the two
benchmark returns (universe cross-sectional mean and the benchmark index), assigns
a leakage-safe train/embargo/test split, and overwrites ml.training_dataset.

Run (from the repo root):
    spark-submit \
      --packages org.apache.iceberg:iceberg-spark-runtime-4.1_2.13:1.11.0 \
      spark/jobs/build_training.py

Prereq: `spark-sql ... -f iceberg/schemas/gold-schema.sql` has been run, and the
benchmark index has been backfilled (poller/backfill.py --include-index).
"""

import argparse
import os
import sys

from pyspark.sql import SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import (
    DateType,
    DoubleType,
    LongType,
    StringType,
    StructField,
    StructType,
)

_HERE = os.path.dirname(os.path.abspath(__file__))
_FEATURES_DIR = os.path.abspath(os.path.join(_HERE, "..", "..", "ml", "feature_engineering"))
sys.path.insert(0, _FEATURES_DIR)
from features import (  # noqa: E402
    BENCHMARK_SYMBOLS,
    FEATURE_COLUMNS,
    HORIZONS,
    LABEL_COLUMNS,
    compute_labels,
    compute_split_dates,
)

INPUT_FIELDS = [
    StructField("symbol", StringType()),
    StructField("trade_date", DateType()),
    StructField("close", DoubleType()),
    StructField("volume", LongType()),
] + [StructField(c, DoubleType()) for c in FEATURE_COLUMNS]

DATASET_FIELDS = INPUT_FIELDS + [StructField(c, DoubleType()) for c in LABEL_COLUMNS]

BENCHMARK_FIELDS = (
    [StructField(f"universe_ret_{h}d", DoubleType()) for h in HORIZONS]
    + [StructField(f"index_ret_{h}d", DoubleType()) for h in HORIZONS]
    + [StructField("split", StringType())]
)

FINAL_COLUMNS = [f.name for f in DATASET_FIELDS] + [f.name for f in BENCHMARK_FIELDS]


def build_spark(warehouse: str) -> SparkSession:
    spark = (
        SparkSession.builder.appName("build_training")
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
    spark.sparkContext.addPyFile(os.path.join(_FEATURES_DIR, "features.py"))
    return spark


def build_dataset(spark: SparkSession):
    silver = spark.table("iceberg.silver.quotes_enriched")
    base = (
        silver.select(
            "symbol",
            "trade_date",
            F.col("close").cast("double").alias("close"),
            F.col("volume").cast("long").alias("volume"),
            *[F.col(c).cast("double").alias(c) for c in FEATURE_COLUMNS],
        )
        .dropna(subset=["close"])
    )

    labeled = base.groupBy("symbol").applyInPandas(
        compute_labels, schema=StructType(DATASET_FIELDS)
    )
    labeled.cache()

    dates = [
        row["trade_date"]
        for row in labeled.select("trade_date").distinct().orderBy("trade_date").collect()
    ]
    test_start, embargo_start = compute_split_dates(dates)

    is_benchmark = F.col("symbol").isin(*BENCHMARK_SYMBOLS)
    universe = labeled.filter(~is_benchmark)
    index = labeled.filter(is_benchmark)

    universe_bench = universe.groupBy("trade_date").agg(
        *[F.avg(c).alias(f"universe_ret_{h}d") for h, c in zip(HORIZONS, LABEL_COLUMNS)]
    )
    index_bench = index.groupBy("trade_date").agg(
        *[F.first(c).alias(f"index_ret_{h}d") for h, c in zip(HORIZONS, LABEL_COLUMNS)]
    )

    dataset = (
        universe.join(universe_bench, "trade_date", "left")
        .join(index_bench, "trade_date", "left")
        .withColumn(
            "split",
            F.when(F.col("trade_date") >= F.lit(str(test_start)).cast("date"), F.lit("test"))
            .when(F.col("trade_date") >= F.lit(str(embargo_start)).cast("date"), F.lit("embargo"))
            .otherwise(F.lit("train")),
        )
    )
    return dataset.select(*FINAL_COLUMNS), test_start, embargo_start


def main():
    parser = argparse.ArgumentParser(description="Build ml.training_dataset from silver")
    parser.add_argument(
        "--warehouse", default="hdfs://localhost:9000/warehouse", help="Iceberg warehouse URI"
    )
    args = parser.parse_args()

    spark = build_spark(args.warehouse)
    spark.sparkContext.setLogLevel("WARN")

    dataset, test_start, embargo_start = build_dataset(spark)
    dataset.createOrReplaceTempView("training_stage")
    spark.sql(
        "INSERT OVERWRITE iceberg.ml.training_dataset SELECT * FROM training_stage"
    )

    total = spark.table("iceberg.ml.training_dataset").count()
    splits = {
        row["split"]: row["count"]
        for row in spark.table("iceberg.ml.training_dataset")
        .groupBy("split")
        .count()
        .collect()
    }
    print(f"ml.training_dataset rows: {total} {splits}")
    print(f"test_start={test_start} embargo_start={embargo_start}")

    spark.stop()


if __name__ == "__main__":
    main()
