"""
train_model.py — train GBT / RandomForest / Linear regressors for each
forward-return label on ml.training_dataset and save them to ml/models/.

Training uses only the `train` split. The `embargo` rows are deliberately
excluded so the label window of the last training row cannot overlap the test
period (docs/project-spec.md §15). Evaluation/model selection is a separate step
(ml/evaluation/evaluate.py) so the interpretable rule baseline can be compared
on equal footing.

Run (from the repo root):
    spark-submit \
      --packages org.apache.iceberg:iceberg-spark-runtime-4.1_2.13:1.11.0 \
      ml/training/train_model.py

Writes: ml/models/<algo>_<label>_<timestamp>/ and ml/models/registry.json
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

from pyspark.ml.feature import VectorAssembler
from pyspark.ml.regression import GBTRegressor, LinearRegression, RandomForestRegressor
from pyspark.sql import SparkSession
from pyspark.sql import functions as F

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
_FEATURES_DIR = os.path.join(_REPO_ROOT, "ml", "feature_engineering")
sys.path.insert(0, _FEATURES_DIR)
from features import LABEL_COLUMNS, MODEL_FEATURES  # noqa: E402

DEFAULT_MODELS_DIR = os.path.join(_REPO_ROOT, "ml", "models")


def local_uri(path: str) -> str:
    """Spark ML save/load resolves scheme-less paths against the default FS (HDFS
    here). Prefix with file:// so model artifacts land in the repo working tree."""
    return "file://" + os.path.abspath(path)


def build_spark(warehouse: str) -> SparkSession:
    return (
        SparkSession.builder.appName("train_model")
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


def estimator(algo: str, label_col: str):
    common = {"featuresCol": "features", "labelCol": "label", "predictionCol": "prediction"}
    if algo == "gbt":
        return GBTRegressor(maxIter=50, maxDepth=4, stepSize=0.05, seed=42, **common)
    if algo == "rf":
        return RandomForestRegressor(numTrees=50, maxDepth=6, seed=42, **common)
    if algo == "linear":
        return LinearRegression(regParam=0.1, elasticNetParam=0.0, **common)
    raise ValueError(f"unknown algo: {algo}")


ALGOS = ["gbt", "rf", "linear"]

# Bound concurrent tree-building tasks so training fits a local single-JVM run.
TRAIN_PARTITIONS = 8


def main():
    parser = argparse.ArgumentParser(description="Train stock-scoring models")
    parser.add_argument("--warehouse", default="hdfs://localhost:9000/warehouse")
    parser.add_argument("--models-dir", default=DEFAULT_MODELS_DIR)
    args = parser.parse_args()

    spark = build_spark(args.warehouse)
    spark.sparkContext.setLogLevel("WARN")

    dataset = spark.table("iceberg.ml.training_dataset").cache()

    test_start = dataset.filter(F.col("split") == "test").agg(F.min("trade_date")).first()[0]
    embargo_start = dataset.filter(F.col("split") == "embargo").agg(F.min("trade_date")).first()[0]

    os.makedirs(args.models_dir, exist_ok=True)
    run_ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    trained_at = datetime.now(timezone.utc).isoformat()

    registry = {
        "trained_at": trained_at,
        "test_start": str(test_start),
        "embargo_start": str(embargo_start),
        "features": MODEL_FEATURES,
        "labels": {},
    }

    for label in LABEL_COLUMNS:
        assembler = VectorAssembler(
            inputCols=MODEL_FEATURES, outputCol="features", handleInvalid="skip"
        )
        prepared = assembler.transform(
            dataset.select(*MODEL_FEATURES, F.col(label).alias("label"), "split")
        )
        train_df = prepared.filter(F.col("split") == "train").repartition(TRAIN_PARTITIONS)
        train_rows = train_df.count()

        registry["labels"][label] = {}
        for algo in ALGOS:
            model = estimator(algo, label)
            fitted = model.fit(train_df)

            version = f"{algo}_{label}_{run_ts}"
            path = os.path.join(args.models_dir, version)
            fitted.write().overwrite().save(local_uri(path))

            registry["labels"][label][algo] = {
                "path": local_uri(path),
                "version": version,
                "train_rows": train_rows,
            }
            print(f"trained {version} on {train_rows} rows")

    registry_path = os.path.join(args.models_dir, "registry.json")
    with open(registry_path, "w") as fh:
        json.dump(registry, fh, indent=2, default=str)
    print(f"wrote {registry_path}")

    spark.stop()


if __name__ == "__main__":
    main()
