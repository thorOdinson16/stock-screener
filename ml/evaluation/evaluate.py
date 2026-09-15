"""
evaluate.py — evaluate every registered model plus the rule-based baseline on the
held-out test split, then select the best ML model per label.

Produces:
  ml/evaluation/results/comparison.json  full metrics
  ml/evaluation/results/comparison.md    readable summary
  ml/models/selected.json                label -> chosen ML model (consumed by
                                         spark/jobs/score_stocks.py)

Metrics (docs/project-spec.md §15): Information Coefficient, Precision@K against
both benchmarks (universe cross-sectional mean and the benchmark index), RMSE/MAE,
mean top-K forward return, and top-K turnover.

Run (from the repo root):
    spark-submit \
      --packages org.apache.iceberg:iceberg-spark-runtime-4.1_2.13:1.11.0 \
      ml/evaluation/evaluate.py
"""

import argparse
import json
import math
import os
import sys
from datetime import datetime, timezone

from pyspark.ml.feature import VectorAssembler
from pyspark.ml.regression import (
    GBTRegressionModel,
    LinearRegressionModel,
    RandomForestRegressionModel,
)
from pyspark.sql import SparkSession
from pyspark.sql import functions as F

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
sys.path.insert(0, os.path.join(_REPO_ROOT, "ml", "feature_engineering"))
sys.path.insert(0, os.path.join(_REPO_ROOT, "ml", "evaluation"))

from features import MODEL_FEATURES  # noqa: E402
from metrics import (  # noqa: E402
    benchmark_forward_return,
    information_coefficient,
    precision_at_k,
    regression_metrics,
    summarize_forward_returns,
    top_k_forward_returns,
    turnover,
)
from rules import rule_score  # noqa: E402

DEFAULT_MODELS_DIR = os.path.join(_REPO_ROOT, "ml", "models")
DEFAULT_RESULTS_DIR = os.path.join(_HERE, "results")
DEFAULT_K = 20

MODEL_LOADERS = {
    "gbt": GBTRegressionModel,
    "rf": RandomForestRegressionModel,
    "linear": LinearRegressionModel,
}


def build_spark(warehouse: str) -> SparkSession:
    return (
        SparkSession.builder.appName("evaluate_models")
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


def _clean(obj):
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean(v) for v in obj]
    if isinstance(obj, float) and math.isnan(obj):
        return None
    return obj


def evaluate_scores(pdf, score_col, ret_col, universe_col, index_col, k):
    ic = information_coefficient(pdf, score_col, ret_col)
    top = top_k_forward_returns(pdf, score_col, ret_col, k=k)
    return {
        **ic,
        "rmse": regression_metrics(pdf, score_col, ret_col)["rmse"],
        "mae": regression_metrics(pdf, score_col, ret_col)["mae"],
        "precision_at_k_vs_universe": precision_at_k(
            pdf, score_col, ret_col, k=k, benchmark_col=universe_col
        )["precision_at_k"],
        "precision_at_k_vs_index": precision_at_k(
            pdf, score_col, ret_col, k=k, benchmark_col=index_col
        )["precision_at_k"],
        "top_k_mean_forward_return": summarize_forward_returns(top)["mean"],
        "universe_mean_forward_return": benchmark_forward_return(pdf, universe_col),
        "index_mean_forward_return": benchmark_forward_return(pdf, index_col),
        "top_k_turnover": turnover(pdf, score_col, k=k),
    }


def select_best(label_results, model_names):
    """Best ML model by IC mean; ties broken by Precision@K vs universe."""
    candidates = {name: label_results[name] for name in model_names if name in label_results}

    def key(item):
        metrics = item[1]
        ic = metrics.get("ic_mean")
        precision = metrics.get("precision_at_k_vs_universe")
        return (
            ic if ic is not None else -math.inf,
            precision if precision is not None else -math.inf,
        )

    return max(candidates.items(), key=key)[0] if candidates else None


def main():
    parser = argparse.ArgumentParser(description="Evaluate and select scoring models")
    parser.add_argument("--warehouse", default="hdfs://localhost:9000/warehouse")
    parser.add_argument("--models-dir", default=DEFAULT_MODELS_DIR)
    parser.add_argument("--results-dir", default=DEFAULT_RESULTS_DIR)
    parser.add_argument("--k", type=int, default=DEFAULT_K)
    args = parser.parse_args()

    registry_path = os.path.join(args.models_dir, "registry.json")
    with open(registry_path) as fh:
        registry = json.load(fh)

    spark = build_spark(args.warehouse)
    spark.sparkContext.setLogLevel("WARN")
    dataset = spark.table("iceberg.ml.training_dataset").cache()

    os.makedirs(args.results_dir, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()

    results = {"evaluated_at": now, "k": args.k, "labels": {}}
    selected = {"selected_at": now, "labels": {}}

    for label, models in registry["labels"].items():
        horizon = label.replace("fwd_ret_", "")
        universe_col = f"universe_ret_{horizon}"
        index_col = f"index_ret_{horizon}"

        test = dataset.filter(F.col("split") == "test").select(
            *MODEL_FEATURES,
            "close",
            F.col(label).alias("label"),
            "symbol",
            "trade_date",
            universe_col,
            index_col,
        )
        assembler = VectorAssembler(
            inputCols=MODEL_FEATURES, outputCol="features", handleInvalid="skip"
        )
        test_prepared = assembler.transform(test)

        label_results = {}

        baseline_pdf = test.toPandas()
        baseline_pdf["prediction"] = rule_score(baseline_pdf)
        label_results["rule_baseline"] = evaluate_scores(
            baseline_pdf, "prediction", "label", universe_col, index_col, args.k
        )

        for algo, meta in models.items():
            model = MODEL_LOADERS[algo].load(meta["path"])
            preds = model.transform(test_prepared).select(
                "symbol",
                "trade_date",
                F.col("label"),
                universe_col,
                index_col,
                "prediction",
            )
            label_results[algo] = evaluate_scores(
                preds.toPandas(), "prediction", "label", universe_col, index_col, args.k
            )

        results["labels"][label] = label_results

        best = select_best(label_results, list(models.keys()))
        if best is not None:
            selected["labels"][label] = {
                "model_name": best,
                "path": models[best]["path"],
                "version": models[best]["version"],
            }
        print(f"{label}: " + ", ".join(
            f"{name} IC={m.get('ic_mean')}" for name, m in label_results.items()
        ))

    comparison_path = os.path.join(args.results_dir, "comparison.json")
    with open(comparison_path, "w") as fh:
        json.dump(_clean(results), fh, indent=2)

    selected_path = os.path.join(args.models_dir, "selected.json")
    with open(selected_path, "w") as fh:
        json.dump(_clean(selected), fh, indent=2)

    _write_markdown(results, os.path.join(args.results_dir, "comparison.md"))
    print(f"wrote {comparison_path}")
    print(f"wrote {selected_path}")

    spark.stop()


def _write_markdown(results, path):
    lines = [
        "# Model Comparison",
        "",
        f"Evaluated: {results['evaluated_at']}  |  K = {results['k']}",
        "",
        "`rule_baseline` is the interpretable technical screen (spec §13); it is not a",
        "candidate for production scoring.",
        "",
    ]
    columns = [
        ("ic_mean", "IC mean"),
        ("ic_ir", "IC IR"),
        ("precision_at_k_vs_universe", "P@K univ"),
        ("precision_at_k_vs_index", "P@K index"),
        ("rmse", "RMSE"),
        ("top_k_mean_forward_return", "TopK fwd ret"),
        ("universe_mean_forward_return", "Univ fwd ret"),
        ("index_mean_forward_return", "Index fwd ret"),
        ("top_k_turnover", "Turnover"),
    ]
    for label, label_results in results["labels"].items():
        lines.append(f"## {label}")
        lines.append("")
        lines.append("| model | " + " | ".join(name for _, name in columns) + " |")
        lines.append("|---|" + "|".join("---" for _ in columns) + "|")
        for model_name, metrics in label_results.items():
            cells = []
            for key, _ in columns:
                value = metrics.get(key)
                if value is None or (isinstance(value, float) and math.isnan(value)):
                    cells.append("")
                else:
                    cells.append(f"{value:.4f}")
            lines.append(f"| {model_name} | " + " | ".join(cells) + " |")
        lines.append("")

    with open(path, "w") as fh:
        fh.write("\n".join(lines))


if __name__ == "__main__":
    main()
