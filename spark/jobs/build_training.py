"""
build_training.py — silver -> ml.training_dataset.

Reads silver.quotes_enriched, computes raw forward returns per symbol, applies
the shared scale-free/cross-sectional transforms from
ml/feature_engineering/transform.py, attaches `industry`, the two benchmark
returns (universe cross-sectional mean and the benchmark index) and the
cross-sectional excess-return labels, assigns a leakage-safe train/embargo/test
split, and overwrites ml.training_dataset.

Feature derivation/normalization is delegated to transform.transform_group so
that the exact same code path runs at scoring time
(spark/jobs/score_stocks.py) — train/serve parity by construction.

Run (from the repo root):
    spark-submit \
      --packages org.apache.iceberg:iceberg-spark-runtime-4.1_2.13:1.11.0 \
      spark/jobs/build_training.py

Prereq: the benchmark index has been backfilled
(poller/backfill.py --include-index).
"""

import argparse
import json
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
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
_FEATURES_DIR = os.path.join(_REPO_ROOT, "ml", "feature_engineering")
sys.path.insert(0, _FEATURES_DIR)
from features import (  # noqa: E402
    BENCHMARK_SYMBOLS,
    HORIZONS,
    LABEL_COLUMNS,
    RAW_FEATURE_COLUMNS,
    RAW_LABEL_COLUMNS,
    compute_labels,
    compute_split_dates,
)
from spark_schema import NORMALIZED_SCHEMA  # noqa: E402
from transform import MODEL_FEATURES, transform_group  # noqa: E402

UNIVERSE_CACHE = os.path.join(_REPO_ROOT, "poller", "universe", "universe_cache.json")

INPUT_FIELDS = [
    StructField("symbol", StringType()),
    StructField("trade_date", DateType()),
    StructField("close", DoubleType()),
    StructField("volume", LongType()),
] + [StructField(c, DoubleType()) for c in RAW_FEATURE_COLUMNS]

DATASET_FIELDS = INPUT_FIELDS + [
    StructField(c, DoubleType()) for c in RAW_LABEL_COLUMNS
]

# Final column order (must match the DDL below).
FINAL_COLUMNS = (
    ["symbol", "trade_date", "industry", "close", "volume"]
    + MODEL_FEATURES
    + RAW_LABEL_COLUMNS
    + LABEL_COLUMNS
    + [f"universe_ret_{h}d" for h in HORIZONS]
    + [f"index_ret_{h}d" for h in HORIZONS]
    + ["split"]
)

_TRAINING_COLUMNS = (
    ["symbol STRING NOT NULL", "trade_date DATE NOT NULL", "industry STRING",
     "close DOUBLE", "volume BIGINT"]
    + [f"{c} DOUBLE" for c in MODEL_FEATURES]
    + [f"{c} DOUBLE" for c in RAW_LABEL_COLUMNS]
    + [f"{c} DOUBLE" for c in LABEL_COLUMNS]
    + [f"universe_ret_{h}d DOUBLE" for h in HORIZONS]
    + [f"index_ret_{h}d DOUBLE" for h in HORIZONS]
    + ["split STRING NOT NULL"]
)

TRAINING_DDL = (
    "CREATE TABLE IF NOT EXISTS iceberg.ml.training_dataset (\n  "
    + ",\n  ".join(_TRAINING_COLUMNS)
    + "\n)\nUSING iceberg\nLOCATION '/warehouse/ml/training_dataset'\n"
    "TBLPROPERTIES ('format-version'='2', 'write.parquet.compression-codec'='snappy')"
)


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
    for module in ("features.py", "transform.py"):
        spark.sparkContext.addPyFile(os.path.join(_FEATURES_DIR, module))
    return spark


def ensure_training_table(spark: SparkSession) -> None:
    """ml.training_dataset is fully derived; if its schema has changed since the
    last build, recreate it (one-time migration)."""
    try:
        existing = spark.table("iceberg.ml.training_dataset").columns
    except Exception:  # noqa: BLE001 — table does not exist yet
        existing = None

    if existing == FINAL_COLUMNS:
        return
    if existing is not None:
        print("ml.training_dataset schema changed; recreating table.")
        spark.sql("DROP TABLE IF EXISTS iceberg.ml.training_dataset")
    spark.sql(TRAINING_DDL)


def universe_metadata(spark: SparkSession):
    """symbol -> industry, from the poller's cached NIFTY 500 list."""
    if not os.path.exists(UNIVERSE_CACHE):
        print(f"WARNING: {UNIVERSE_CACHE} missing — industry will be 'Unknown'.")
        return spark.createDataFrame([], "symbol string, industry string")
    with open(UNIVERSE_CACHE) as fh:
        stocks = json.load(fh)["stocks"]
    rows = [(s["yf_symbol"], s.get("industry") or "Unknown") for s in stocks]
    return spark.createDataFrame(rows, ["symbol", "industry"])


def build_dataset(spark: SparkSession):
    silver = spark.table("iceberg.silver.quotes_enriched")
    base = (
        silver.select(
            "symbol",
            "trade_date",
            F.col("close").cast("double").alias("close"),
            F.col("volume").cast("long").alias("volume"),
            *[F.col(c).cast("double").alias(c) for c in RAW_FEATURE_COLUMNS],
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

    # Shared, scale-free + cross-sectionally normalized model features.
    raw_for_transform = base.select("symbol", "trade_date", "close", *RAW_FEATURE_COLUMNS)
    features = raw_for_transform.groupBy("trade_date").applyInPandas(
        transform_group, schema=NORMALIZED_SCHEMA
    )

    is_benchmark = F.col("symbol").isin(*BENCHMARK_SYMBOLS)
    universe = labeled.filter(~is_benchmark)
    index = labeled.filter(is_benchmark)

    universe_bench = universe.groupBy("trade_date").agg(
        *[F.avg(c).alias(f"universe_ret_{h}d") for h, c in zip(HORIZONS, RAW_LABEL_COLUMNS)]
    )
    index_bench = index.groupBy("trade_date").agg(
        *[F.first(c).alias(f"index_ret_{h}d") for h, c in zip(HORIZONS, RAW_LABEL_COLUMNS)]
    )

    core = (
        labeled.select("symbol", "trade_date", "close", "volume", *RAW_LABEL_COLUMNS)
        .join(features, ["symbol", "trade_date"], "inner")
        .join(F.broadcast(universe_metadata(spark)), "symbol", "left")
        .fillna({"industry": "Unknown"})
        .join(universe_bench, "trade_date", "left")
        .join(index_bench, "trade_date", "left")
    )
    for h, raw, excess in zip(HORIZONS, RAW_LABEL_COLUMNS, LABEL_COLUMNS):
        core = core.withColumn(excess, F.col(raw) - F.col(f"universe_ret_{h}d"))

    dataset = core.withColumn(
        "split",
        F.when(F.col("trade_date") >= F.lit(str(test_start)).cast("date"), F.lit("test"))
        .when(F.col("trade_date") >= F.lit(str(embargo_start)).cast("date"), F.lit("embargo"))
        .otherwise(F.lit("train")),
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

    ensure_training_table(spark)
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
