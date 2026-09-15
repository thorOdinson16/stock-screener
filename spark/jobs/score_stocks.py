"""
score_stocks.py — score the latest cross-section of the universe with the
models selected by ml/evaluation/evaluate.py.

Produces:
  gold.stock_scores   one row per (symbol, label) for the scoring date
  gold.top_picks      top-N per label for the scoring date
  market.scores       Kafka topic (optional; requires the spark-sql-kafka package)

Idempotent: existing rows for the scoring date are deleted before insert, so the
job can be re-run on the same day without duplicating.

Run (from the repo root):
    spark-submit \
      --packages org.apache.iceberg:iceberg-spark-runtime-4.1_2.13:1.11.0,org.apache.spark:spark-sql-kafka-0-10_2.13:4.1.3 \
      spark/jobs/score_stocks.py
"""

import argparse
import json
import os
import sys

from pyspark.ml.feature import VectorAssembler
from pyspark.ml.regression import (
    GBTRegressionModel,
    LinearRegressionModel,
    RandomForestRegressionModel,
)
from pyspark.sql import SparkSession, Window
from pyspark.sql import functions as F

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
sys.path.insert(0, os.path.join(_REPO_ROOT, "ml", "feature_engineering"))
from features import BENCHMARK_SYMBOLS, MODEL_FEATURES  # noqa: E402

DEFAULT_SELECTED = os.path.join(_REPO_ROOT, "ml", "models", "selected.json")

MODEL_LOADERS = {
    "gbt": GBTRegressionModel,
    "rf": RandomForestRegressionModel,
    "linear": LinearRegressionModel,
}


def build_spark(warehouse: str) -> SparkSession:
    return (
        SparkSession.builder.appName("score_stocks")
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


def latest_cross_section(spark: SparkSession):
    silver = spark.table("iceberg.silver.quotes_enriched")
    universe = silver.filter(~F.col("symbol").isin(*BENCHMARK_SYMBOLS))
    score_date = universe.agg(F.max("trade_date")).first()[0]

    base = (
        universe.filter(F.col("trade_date") == F.lit(score_date))
        .select(
            "symbol",
            "trade_date",
            *[F.col(c).cast("double").alias(c) for c in MODEL_FEATURES],
        )
    )
    return base, score_date


def scores_for_label(base, meta, label):
    model = MODEL_LOADERS[meta["model_name"]].load(meta["path"])
    assembler = VectorAssembler(
        inputCols=MODEL_FEATURES, outputCol="features", handleInvalid="skip"
    )
    scored = model.transform(assembler.transform(base))
    return scored.select(
        "symbol",
        "trade_date",
        F.lit(label).alias("label"),
        F.col("prediction").alias("score"),
        F.lit(meta["model_name"]).alias("model_name"),
        F.lit(meta["version"]).alias("model_version"),
    )


def publish_to_kafka(scores, bootstrap_servers: str, topic: str = "market.scores") -> None:
    payload = scores.select(
        F.col("symbol").alias("key"),
        F.to_json(
            F.struct(
                "symbol",
                "trade_date",
                "label",
                "score",
                "rank",
                "model_name",
                "model_version",
                "scored_at",
            )
        ).alias("value"),
    )
    (
        payload.write.format("kafka")
        .option("kafka.bootstrap.servers", bootstrap_servers)
        .option("topic", topic)
        .save()
    )


def main():
    parser = argparse.ArgumentParser(description="Score the latest universe cross-section")
    parser.add_argument("--warehouse", default="hdfs://localhost:9000/warehouse")
    parser.add_argument("--selected", default=DEFAULT_SELECTED)
    parser.add_argument("--bootstrap-servers", default="localhost:9092")
    parser.add_argument("--top-k", type=int, default=20)
    parser.add_argument(
        "--skip-publish", action="store_true", help="Do not publish to market.scores"
    )
    args = parser.parse_args()

    if not os.path.exists(args.selected):
        raise SystemExit(
            f"{args.selected} not found — run ml/evaluation/evaluate.py first."
        )
    with open(args.selected) as fh:
        selected = json.load(fh)

    spark = build_spark(args.warehouse)
    spark.sparkContext.setLogLevel("WARN")

    base, score_date = latest_cross_section(spark)
    universe_size = base.count()

    frames = [
        scores_for_label(base, meta, label)
        for label, meta in selected["labels"].items()
    ]
    if not frames:
        raise SystemExit("selected.json contains no labels to score.")

    scores = frames[0]
    for frame in frames[1:]:
        scores = scores.unionByName(frame)

    window = Window.partitionBy("label").orderBy(F.col("score").desc())
    scores = (
        scores.withColumn("rank", F.row_number().over(window))
        .withColumn("scored_at", F.current_timestamp())
        .cache()
    )

    scores.createOrReplaceTempView("scores_stage")
    spark.sql(
        f"DELETE FROM iceberg.gold.stock_scores WHERE trade_date = DATE '{score_date}'"
    )
    spark.sql(
        "INSERT INTO iceberg.gold.stock_scores "
        "SELECT symbol, trade_date, label, score, `rank`, model_name, model_version, scored_at "
        "FROM scores_stage"
    )

    top = scores.filter(F.col("rank") <= args.top_k).select(
        "trade_date", "label", "rank", "symbol", "score", "model_name", "scored_at"
    )
    top.createOrReplaceTempView("top_picks_stage")
    spark.sql(
        f"DELETE FROM iceberg.gold.top_picks WHERE trade_date = DATE '{score_date}'"
    )
    spark.sql(
        "INSERT INTO iceberg.gold.top_picks "
        "SELECT trade_date, label, `rank`, symbol, score, model_name, scored_at "
        "FROM top_picks_stage"
    )

    print(f"Scored {universe_size} symbols for {score_date}; wrote gold.stock_scores + gold.top_picks.")

    if not args.skip_publish:
        try:
            publish_to_kafka(scores, args.bootstrap_servers)
            print("Published scores to market.scores.")
        except Exception as e:  # noqa: BLE001 — table writes already succeeded
            print(f"Kafka publish skipped ({e}); gold tables were written successfully.")

    spark.stop()


if __name__ == "__main__":
    main()
