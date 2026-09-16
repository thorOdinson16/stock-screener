"""
train_model.py — train GBT / RandomForest / Linear regressors for each
cross-sectional excess-return label on ml.training_dataset and save them to
ml/models/.

The model features are the shared, scale-free and cross-sectionally normalized
features from ml/feature_engineering/transform.py (MODEL_FEATURES). Each
algorithm is tuned on a time-ordered validation slice carved from the `train`
split (with an embargo of max(HORIZONS) days between fit and validation), then
refit on the full train split. Feature importances/coefficients are recorded in
ml/models/registry.json.

Training uses only the `train` split; `embargo` and `test` rows are excluded.
Evaluation/model selection is a separate step (ml/evaluation/evaluate.py).

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

from pyspark.ml.evaluation import RegressionEvaluator
from pyspark.ml.feature import VectorAssembler
from pyspark.ml.regression import (
    GBTRegressor,
    LinearRegression,
    RandomForestRegressor,
)
from pyspark.sql import SparkSession
from pyspark.sql import functions as F

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
_FEATURES_DIR = os.path.join(_REPO_ROOT, "ml", "feature_engineering")
sys.path.insert(0, _FEATURES_DIR)
from features import HORIZONS, LABEL_COLUMNS  # noqa: E402
from transform import MODEL_FEATURES  # noqa: E402

DEFAULT_MODELS_DIR = os.path.join(_REPO_ROOT, "ml", "models")

# Small hyperparameter grids (tuned on the validation slice).
GRIDS = {
    "gbt": [
        {"maxIter": 30, "maxDepth": 3},
        {"maxIter": 50, "maxDepth": 4},
        {"maxIter": 50, "maxDepth": 5},
    ],
    "rf": [
        {"numTrees": 30, "maxDepth": 5},
        {"numTrees": 60, "maxDepth": 6},
    ],
    "linear": [
        {"regParam": 0.0},
        {"regParam": 0.1},
        {"regParam": 1.0},
    ],
}

ALGOS = ["gbt", "rf", "linear"]
TRAIN_PARTITIONS = 8
VALIDATION_FRACTION = 0.2
EMBARGO_DAYS = max(HORIZONS)


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


def estimator(algo: str, params: dict):
    common = {"featuresCol": "features", "labelCol": "label", "predictionCol": "prediction", "seed": 42}
    if algo == "gbt":
        return GBTRegressor(stepSize=0.05, **params, **common)
    if algo == "rf":
        return RandomForestRegressor(**params, **common)
    if algo == "linear":
        return LinearRegression(elasticNetParam=0.0, **params, **common)
    raise ValueError(f"unknown algo: {algo}")


def time_validation_split(dataset, label: str):
    """Returns (fit, validation): a time-ordered validation tail of the train
    split, with an embargo gap so fit labels cannot overlap validation."""
    train_pool = dataset.filter(F.col("split") == "train")
    dates = [
        row["trade_date"]
        for row in train_pool.select("trade_date").distinct().orderBy("trade_date").collect()
    ]
    n = len(dates)
    if n < 4:
        return train_pool, train_pool

    val_idx = int(round(n * (1.0 - VALIDATION_FRACTION)))
    val_idx = min(max(val_idx, 1), n - 1)
    fit_n = max(1, val_idx - EMBARGO_DAYS)
    fit_cutoff = dates[fit_n]
    val_start = dates[val_idx]

    fit = train_pool.filter(F.col("trade_date") < F.lit(str(fit_cutoff)).cast("date"))
    val = train_pool.filter(F.col("trade_date") >= F.lit(str(val_start)).cast("date"))
    return fit, val


def feature_attribution(fitted, algo: str) -> dict:
    if algo in ("gbt", "rf"):
        values = fitted.featureImportances.toArray()
    elif algo == "linear":
        values = fitted.coefficients.toArray()
    else:
        return {}
    return {name: float(v) for name, v in zip(MODEL_FEATURES, values)}


def assemble(dataset, label: str):
    assembler = VectorAssembler(
        inputCols=MODEL_FEATURES, outputCol="features", handleInvalid="skip"
    )
    return assembler.transform(
        dataset.select(*MODEL_FEATURES, F.col(label).alias("label"), "split")
    )


def tune(algo: str, fit_df, val_df):
    evaluator = RegressionEvaluator(
        featuresCol="features", labelCol="label", predictionCol="prediction", metricName="rmse"
    )
    best_params, best_rmse = None, None
    for params in GRIDS[algo]:
        try:
            model = estimator(algo, params).fit(fit_df)
            rmse = evaluator.evaluate(model.transform(val_df))
        except Exception as e:  # noqa: BLE001 — one bad config must not abort the grid
            print(f"  {algo} {params} failed: {e}")
            continue
        print(f"  {algo} {params} val_rmse={rmse:.5f}")
        if best_rmse is None or rmse < best_rmse:
            best_params, best_rmse = params, rmse
    return best_params, best_rmse


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
        prepared = assemble(dataset, label).cache()
        train_df = prepared.filter(F.col("split") == "train").repartition(TRAIN_PARTITIONS)
        train_rows = train_df.count()

        fit_df, val_df = time_validation_split(dataset, label)
        fit_df = assemble(fit_df, label).repartition(TRAIN_PARTITIONS).cache()
        val_df = assemble(val_df, label).repartition(TRAIN_PARTITIONS).cache()

        registry["labels"][label] = {}
        for algo in ALGOS:
            best_params, val_rmse = tune(algo, fit_df, val_df)
            if best_params is None:
                print(f"skipping {algo}/{label}: no config trained")
                continue

            fitted = estimator(algo, best_params).fit(train_df)

            version = f"{algo}_{label}_{run_ts}"
            path = os.path.join(args.models_dir, version)
            fitted.write().overwrite().save(local_uri(path))

            registry["labels"][label][algo] = {
                "path": local_uri(path),
                "version": version,
                "train_rows": train_rows,
                "params": best_params,
                "validation_rmse": val_rmse,
                "attribution": feature_attribution(fitted, algo),
            }
            print(f"trained {version} on {train_rows} rows (val_rmse={val_rmse:.5f})")

    registry_path = os.path.join(args.models_dir, "registry.json")
    with open(registry_path, "w") as fh:
        json.dump(registry, fh, indent=2, default=str)
    print(f"wrote {registry_path}")

    spark.stop()


if __name__ == "__main__":
    main()
