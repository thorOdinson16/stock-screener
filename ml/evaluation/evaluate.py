"""
evaluate.py — evaluate every registered model plus the interpretable rule
baseline on the held-out test split, run a walk-forward check for the selected
model, and select the best ML model per label by a risk-adjusted net-of-cost
metric.

Produces:
  ml/evaluation/results/comparison.json  full metrics
  ml/evaluation/results/comparison.md    readable summary
  ml/models/selected.json                label -> chosen ML model (consumed by
                                         spark/jobs/score_stocks.py)
  ml/experiments/<ts>_evaluate.json      reproducible run record

Metrics: IC (vs the excess-return label) with a Newey–West t-stat, a
sector-neutral IC view, Precision@K against the universe and the benchmark
index, top-K turnover, and a non-overlapping, cost-aware long/short backtest
(ml/evaluation/backtest.py) net of 10 bps/side.

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
from pyspark.sql import SparkSession, Window
from pyspark.sql import functions as F

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
sys.path.insert(0, os.path.join(_REPO_ROOT, "ml", "feature_engineering"))
sys.path.insert(0, os.path.join(_REPO_ROOT, "ml", "evaluation"))
sys.path.insert(0, os.path.join(_REPO_ROOT, "ml", "experiments"))
sys.path.insert(0, os.path.join(_REPO_ROOT, "ml", "training"))

from transform import MODEL_FEATURES  # noqa: E402
from metrics import (  # noqa: E402
    benchmark_forward_return,
    information_coefficient,
    precision_at_k,
    regression_metrics,
    summarize_forward_returns,
    top_k_forward_returns,
    turnover,
)
from backtest import DEFAULT_COST_BPS, backtest  # noqa: E402
from walk_forward import (  # noqa: E402
    expanding_folds,
    purged_kfold_folds,
    run_folds,
)
from rules import rule_score  # noqa: E402
from registry import record as record_experiment  # noqa: E402
from train_model import estimator  # noqa: E402

DEFAULT_MODELS_DIR = os.path.join(_REPO_ROOT, "ml", "models")
DEFAULT_RESULTS_DIR = os.path.join(_HERE, "results")
DEFAULT_EXPERIMENTS_DIR = os.path.join(_REPO_ROOT, "ml", "experiments")
DEFAULT_K = 20

MODEL_LOADERS = {
    "gbt": GBTRegressionModel,
    "rf": RandomForestRegressionModel,
    "linear": LinearRegressionModel,
}

ENSEMBLE_NAME = "rank_ensemble"

# Non-model keys inside results["labels"][label] (filled after selection).
FOLD_KEYS = {"walk_forward", "purged_kfold"}


def load_model(meta):
    return MODEL_LOADERS[meta.get("base_algo", "gbt")].load(meta["path"])

# Raw (pre-normalization) inputs the rule baseline thresholds require. `close`
# already comes from the training dataset.
RULE_INPUTS = ["sma_50", "sma_200", "volume_ratio",
               "distance_from_52w_high", "rsi_14", "price_momentum_1m"]


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


def horizon_of(label: str) -> int:
    return int(label.replace("excess_ret_", "").replace("d", ""))


def raw_label(label: str) -> str:
    return label.replace("excess_ret_", "fwd_ret_")


def sector_neutral_ic(pdf, score_col, label_col, lags):
    neutral = pdf.dropna(subset=[score_col, label_col, "industry"]).copy()
    if neutral.empty:
        return {"ic_mean": None, "ic_t_stat": None}
    for col in (score_col, label_col):
        neutral[col] = neutral[col] - neutral.groupby(
            ["trade_date", "industry"]
        )[col].transform("mean")
    return information_coefficient(neutral, score_col, label_col, lags=lags)


def evaluate_scores(pdf, score_col, label_col, raw_col, universe_col, index_col,
                    k, horizon, cost_bps, n_trials=1):
    ic = information_coefficient(pdf, score_col, label_col, lags=horizon - 1)
    neutral = sector_neutral_ic(pdf, score_col, label_col, lags=horizon - 1)
    top = top_k_forward_returns(pdf, score_col, raw_col, k=k)
    return {
        **ic,
        "sector_neutral_ic_mean": neutral.get("ic_mean"),
        "sector_neutral_ic_t_stat": neutral.get("ic_t_stat"),
        "rmse": regression_metrics(pdf, score_col, label_col)["rmse"],
        "mae": regression_metrics(pdf, score_col, label_col)["mae"],
        "precision_at_k_vs_universe": precision_at_k(
            pdf, score_col, raw_col, k=k, benchmark_col=universe_col
        )["precision_at_k"],
        "precision_at_k_vs_index": precision_at_k(
            pdf, score_col, raw_col, k=k, benchmark_col=index_col
        )["precision_at_k"],
        "top_k_mean_forward_return": summarize_forward_returns(top)["mean"],
        "universe_mean_forward_return": benchmark_forward_return(pdf, universe_col),
        "index_mean_forward_return": benchmark_forward_return(pdf, index_col),
        "top_k_turnover": turnover(pdf, score_col, k=k),
        "backtest": backtest(
            pdf, horizon, k=k, cost_bps=cost_bps, long_short=True,
            ret_col=raw_col, score_col=score_col,
            universe_ret_col=universe_col, index_ret_col=index_col,
            n_trials=n_trials,
        ),
    }


def predict_frame(model, test_prepared, label, raw, universe_col, index_col):
    return model.transform(test_prepared).select(
        "symbol", "trade_date", "industry",
        F.col(label).alias(label), F.col(raw), universe_col, index_col,
        F.col("prediction"),
    )


def selected_members(best, models):
    if best == ENSEMBLE_NAME:
        return [
            {"algo": name, **models[name]} for name in ("gbt_rank", "rf_rank")
        ]
    return [{"algo": best, **models[best]}]


def selected_entry(best, models):
    if best == ENSEMBLE_NAME:
        return {
            "model_name": ENSEMBLE_NAME,
            "members": [
                {"model_name": name, "path": models[name]["path"],
                 "base_algo": models[name].get("base_algo", "gbt")}
                for name in ("gbt_rank", "rf_rank")
            ],
        }
    return {
        "model_name": best,
        "path": models[best]["path"],
        "version": models[best]["version"],
    }


def ensemble_frame(models, test_prepared, test, label, raw, universe_col, index_col):
    base = test.select(
        "symbol", "trade_date", "industry",
        F.col(label).alias(label), raw, universe_col, index_col,
    )
    g = predict_frame(load_model(models["gbt_rank"]), test_prepared, label, raw,
                      universe_col, index_col)
    r = predict_frame(load_model(models["rf_rank"]), test_prepared, label, raw,
                      universe_col, index_col)
    return (
        base.join(g.select("symbol", "trade_date", F.col("prediction").alias("p_gbt")),
                  ["symbol", "trade_date"])
        .join(r.select("symbol", "trade_date", F.col("prediction").alias("p_rf")),
              ["symbol", "trade_date"])
        .withColumn("prediction", (F.col("p_gbt") + F.col("p_rf")) / 2.0)
    )


def select_best(label_results, model_names):
    """Best ML model by net-of-cost long/short Sharpe; ties broken by IC t-stat."""
    candidates = {name: label_results[name] for name in model_names if name in label_results}
    if not candidates:
        return None

    def key(item):
        metrics = item[1]
        sharpe = (metrics.get("backtest") or {}).get("net_sharpe")
        tstat = metrics.get("ic_t_stat")
        return (
            sharpe if sharpe is not None else -math.inf,
            tstat if tstat is not None else -math.inf,
        )

    return max(candidates.items(), key=key)[0]


def full_history_symbols(dataset, threshold: float):
    """Symbols present on at least `threshold` of all trading dates. A
    robustness run restricted to these mitigates (does not remove) survivorship
    bias from index additions/removals."""
    n_dates = dataset.select("trade_date").distinct().count()
    if n_dates == 0:
        return []
    return [
        row["symbol"]
        for row in dataset.groupBy("symbol").count().collect()
        if row["count"] >= threshold * n_dates
    ]


def rule_inputs(spark: SparkSession):
    silver = spark.table("iceberg.silver.quotes_enriched")
    return silver.select(
        "symbol",
        "trade_date",
        *[F.col(c).cast("double").alias(c) for c in RULE_INPUTS],
    )


def training_snapshot_id(spark: SparkSession):
    """Latest Iceberg snapshot id of the training dataset, for time-travel
    reproducibility of the recorded experiment."""
    try:
        row = spark.sql(
            "SELECT snapshot_id FROM iceberg.ml.training_dataset.snapshots "
            "ORDER BY committed_at DESC LIMIT 1"
        ).first()
        return str(row["snapshot_id"]) if row else None
    except Exception:  # noqa: BLE001 — metadata may be unavailable
        return None


def fold_metrics(dataset, label, members, k, cost_bps, mode="walk_forward", n_trials=1):
    """Out-of-sample metrics from expanding-window walk-forward or purged
    K-fold folds."""
    horizon = horizon_of(label)
    raw = raw_label(label)
    universe_col, index_col = f"universe_ret_{horizon}d", f"index_ret_{horizon}d"
    rank_col = f"{label}_rank"
    try:
        dates = [
            row["trade_date"]
            for row in dataset.select("trade_date").distinct().orderBy("trade_date").collect()
        ]

        def fit_predict(train_dates, test_dates):
            assembler = VectorAssembler(
                inputCols=MODEL_FEATURES, outputCol="features", handleInvalid="skip"
            )
            train = dataset.filter(F.col("trade_date").isin(train_dates))
            test = dataset.filter(F.col("trade_date").isin(test_dates))
            if any(m.get("kind") == "rank" for m in members):
                window = Window.partitionBy("trade_date").orderBy(label)
                train = train.withColumn(rank_col, F.percent_rank().over(window) - 0.5)
                test = test.withColumn(rank_col, F.percent_rank().over(window) - 0.5)

            base = test.select(
                "symbol", "trade_date", "industry",
                F.col(label).alias(label), raw, universe_col, index_col,
            )
            joined = base
            pcols = []
            for m in members:
                target = rank_col if m.get("kind") == "rank" else label
                model = estimator(m["algo"], dict(m.get("params") or {})).fit(
                    assembler.transform(
                        train.select(*MODEL_FEATURES, F.col(target).alias("label"))
                    )
                )
                alias = f"pred_{m['algo']}"
                prediction = model.transform(
                    assembler.transform(test.select(*MODEL_FEATURES, "symbol", "trade_date"))
                ).select("symbol", "trade_date", F.col("prediction").alias(alias))
                joined = joined.join(prediction, ["symbol", "trade_date"], "inner")
                pcols.append(alias)

            joined = joined.withColumn(
                "score", sum(F.col(c) for c in pcols) / float(len(pcols))
            )
            return joined.select(
                "symbol", "trade_date", "industry",
                F.col(label).alias("label"), raw, universe_col, index_col, "score",
            ).toPandas()

        folds = (
            purged_kfold_folds(dates)
            if mode == "purged_kfold"
            else expanding_folds(dates)
        )
        wf = run_folds(dates, fit_predict, folds)
        if wf.empty:
            return {"mode": mode, "n_periods": 0}
        ic = information_coefficient(wf, "score", label, lags=horizon - 1)
        bt = backtest(
            wf, horizon, k=k, cost_bps=cost_bps, long_short=True,
            ret_col=raw, score_col="score",
            universe_ret_col=universe_col, index_ret_col=index_col,
            n_trials=n_trials,
        )
        return {
            "mode": mode,
            "n_periods": int(len(wf)),
            "ic_mean": ic["ic_mean"],
            "ic_t_stat": ic["ic_t_stat"],
            "ic_p_value": ic["ic_p_value"],
            "net_sharpe": bt.get("net_sharpe"),
            "net_annualized_return": bt.get("net_annualized_return"),
        }
    except Exception as e:  # noqa: BLE001 — fold evaluation is best-effort
        print(f"{mode} for {label} skipped: {e}")
        return {"mode": mode, "error": str(e)}


def main():
    parser = argparse.ArgumentParser(description="Evaluate and select scoring models")
    parser.add_argument("--warehouse", default="hdfs://localhost:9000/warehouse")
    parser.add_argument("--models-dir", default=DEFAULT_MODELS_DIR)
    parser.add_argument("--results-dir", default=DEFAULT_RESULTS_DIR)
    parser.add_argument("--experiments-dir", default=DEFAULT_EXPERIMENTS_DIR)
    parser.add_argument("--k", type=int, default=DEFAULT_K)
    parser.add_argument("--cost-bps", type=float, default=DEFAULT_COST_BPS)
    parser.add_argument("--skip-walk-forward", action="store_true")
    parser.add_argument(
        "--coverage-threshold", type=float, default=0.9,
        help="Fraction of trading dates a symbol must appear on for the "
             "full-history robustness run.",
    )
    parser.add_argument(
        "--purged-kfold", action="store_true",
        help="Also run a purged K-fold evaluation alongside walk-forward.",
    )
    args = parser.parse_args()

    registry_path = os.path.join(args.models_dir, "registry.json")
    with open(registry_path) as fh:
        registry = json.load(fh)

    spark = build_spark(args.warehouse)
    spark.sparkContext.setLogLevel("WARN")
    dataset = spark.table("iceberg.ml.training_dataset").cache()
    rules = rule_inputs(spark).cache()

    os.makedirs(args.results_dir, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()

    # Multiple-testing denominator: every model (plus the ensemble) on every label.
    n_trials = sum(len(models) + 1 for models in registry["labels"].values())

    results = {
        "evaluated_at": now, "k": args.k, "cost_bps": args.cost_bps,
        "n_trials": n_trials, "labels": {},
    }
    selected = {"selected_at": now, "labels": {}}
    robustness = {}
    hf_symbols = full_history_symbols(dataset, args.coverage_threshold)
    print(f"full-history symbols (>= {args.coverage_threshold:.0%} of dates): {len(hf_symbols)}")

    for label, models in registry["labels"].items():
        horizon = horizon_of(label)
        raw = raw_label(label)
        universe_col, index_col = f"universe_ret_{horizon}d", f"index_ret_{horizon}d"

        test = dataset.filter(F.col("split") == "test").select(
            *MODEL_FEATURES, "close", F.col(label).alias(label), "symbol",
            "trade_date", "industry", universe_col, index_col,
        )
        assembler = VectorAssembler(
            inputCols=MODEL_FEATURES, outputCol="features", handleInvalid="skip"
        )
        test_prepared = assembler.transform(test)

        label_results = {}

        # Drop the normalized copies of the rule inputs before joining the raw
        # ones, to avoid duplicate column names.
        overlap = [c for c in RULE_INPUTS if c in test.columns]
        baseline_pdf = (
            test.drop(*overlap).join(rules, on=["symbol", "trade_date"], how="left")
        ).toPandas()
        baseline_pdf["prediction"] = rule_score(baseline_pdf)
        label_results["rule_baseline"] = evaluate_scores(
            baseline_pdf, "prediction", label, raw, universe_col, index_col,
            args.k, horizon, args.cost_bps, n_trials,
        )

        for algo, meta in models.items():
            preds = predict_frame(
                load_model(meta), test_prepared, label, raw, universe_col, index_col
            )
            label_results[algo] = evaluate_scores(
                preds.toPandas(), "prediction", label, raw, universe_col, index_col,
                args.k, horizon, args.cost_bps, n_trials,
            )

        if "gbt_rank" in models and "rf_rank" in models:
            label_results[ENSEMBLE_NAME] = evaluate_scores(
                ensemble_frame(
                    models, test_prepared, test, label, raw, universe_col, index_col
                ).toPandas(),
                "prediction", label, raw, universe_col, index_col,
                args.k, horizon, args.cost_bps, n_trials,
            )

        candidates = list(models.keys()) + (
            [ENSEMBLE_NAME] if ENSEMBLE_NAME in label_results else []
        )
        best = select_best(label_results, candidates)
        if best is not None:
            members = selected_members(best, models)
            selected["labels"][label] = selected_entry(best, models)
            if not args.skip_walk_forward:
                label_results["walk_forward"] = fold_metrics(
                    dataset, label, members, args.k, args.cost_bps,
                    mode="walk_forward", n_trials=n_trials,
                )
                if args.purged_kfold:
                    label_results["purged_kfold"] = fold_metrics(
                        dataset, label, members, args.k, args.cost_bps,
                        mode="purged_kfold", n_trials=n_trials,
                    )
            if hf_symbols:
                hf_test = test_prepared.filter(F.col("symbol").isin(hf_symbols))
                if best == ENSEMBLE_NAME:
                    hf_preds = ensemble_frame(
                        models, hf_test, test.filter(F.col("symbol").isin(hf_symbols)),
                        label, raw, universe_col, index_col,
                    )
                else:
                    hf_preds = predict_frame(
                        load_model(models[best]), hf_test, label, raw,
                        universe_col, index_col,
                    )
                robustness[label] = {
                    "model_name": best,
                    "coverage_threshold": args.coverage_threshold,
                    "n_symbols": len(hf_symbols),
                    **evaluate_scores(
                        hf_preds.toPandas(), "prediction", label, raw,
                        universe_col, index_col, args.k, horizon, args.cost_bps,
                        n_trials,
                    ),
                }

        results["labels"][label] = label_results
        print(f"{label}: selected {best}; " + ", ".join(
            f"{name} IC={m.get('ic_mean')}" for name, m in label_results.items()
            if name not in FOLD_KEYS
        ))

    if robustness:
        results["robustness"] = robustness

    comparison_path = os.path.join(args.results_dir, "comparison.json")
    with open(comparison_path, "w") as fh:
        json.dump(_clean(results), fh, indent=2)

    selected_path = os.path.join(args.models_dir, "selected.json")
    with open(selected_path, "w") as fh:
        json.dump(_clean(selected), fh, indent=2)

    _write_markdown(results, os.path.join(args.results_dir, "comparison.md"))
    record_experiment(
        "evaluate",
        {"k": args.k, "cost_bps": args.cost_bps, "n_trials": n_trials,
         "training_snapshot_id": training_snapshot_id(spark),
         "selected": _clean(selected), "results": _clean(results)},
        _REPO_ROOT,
        args.experiments_dir,
    )
    print(f"wrote {comparison_path}")
    print(f"wrote {selected_path}")

    spark.stop()


def _write_markdown(results, path):
    lines = [
        "# Model Comparison",
        "",
        f"Evaluated: {results['evaluated_at']}  |  K = {results['k']}  |  "
        f"cost = {results['cost_bps']} bps/side",
        "",
        "`rule_baseline` is the interpretable technical screen (spec §13); it is not a",
        "candidate for production scoring. IC is measured against the excess-return",
        "label; the backtest is non-overlapping and net of costs.",
        "",
    ]
    columns = [
        ("ic_mean", "IC mean"),
        ("ic_t_stat", "IC t"),
        ("sector_neutral_ic_mean", "IC sec-neut"),
        ("precision_at_k_vs_universe", "P@K univ"),
        ("precision_at_k_vs_index", "P@K index"),
        ("top_k_turnover", "Turnover"),
    ]
    for label, label_results in results["labels"].items():
        lines.append(f"## {label}")
        lines.append("")
        lines.append("| model | " + " | ".join(name for _, name in columns) + " |")
        lines.append("|---|" + "|".join("---" for _ in columns) + "|")
        for model_name, metrics in label_results.items():
            if model_name in FOLD_KEYS:
                continue
            cells = []
            for key, _ in columns:
                value = metrics.get(key)
                cells.append("" if value is None or (isinstance(value, float) and math.isnan(value))
                             else f"{value:.4f}")
            lines.append(f"| {model_name} | " + " | ".join(cells) + " |")
        lines.append("")
        lines.append("| model | net Sharpe | net ann ret | net max DD | gross Sharpe | avg turnover |")
        lines.append("|---|---|---|---|---|---|")
        for model_name, metrics in label_results.items():
            if model_name in FOLD_KEYS:
                continue
            bt = metrics.get("backtest") or {}
            lines.append(
                f"| {model_name} | {_fmt(bt.get('net_sharpe'))} | "
                f"{_fmt(bt.get('net_annualized_return'))} | {_fmt(bt.get('net_max_drawdown'))} | "
                f"{_fmt(bt.get('gross_sharpe'))} | {_fmt(bt.get('avg_turnover'))} |"
            )
        for key, title in (("walk_forward", "Walk-forward"),
                           ("purged_kfold", "Purged K-fold")):
            fold = label_results.get(key)
            if fold:
                lines.append("")
                if fold.get("error"):
                    lines.append(f"{title} (selected model): skipped ({fold['error']}).")
                else:
                    lines.append(
                        f"{title} (selected model): {fold.get('n_periods', 0)} periods, "
                        f"IC {_fmt(fold.get('ic_mean'))} (t={_fmt(fold.get('ic_t_stat'))}), "
                        f"net Sharpe {_fmt(fold.get('net_sharpe'))}."
                    )
        lines.append("")

    if results.get("robustness"):
        lines.append("## Robustness: symbols with full history")
        lines.append("")
        lines.append("| label | model | symbols | IC | IC t | net Sharpe |")
        lines.append("|---|---|---|---|---|---|")
        for label, r in results["robustness"].items():
            bt = r.get("backtest") or {}
            lines.append(
                f"| {label} | {r.get('model_name')} | {r.get('n_symbols')} | "
                f"{_fmt(r.get('ic_mean'))} | {_fmt(r.get('ic_t_stat'))} | "
                f"{_fmt(bt.get('net_sharpe'))} |"
            )
        lines.append("")

    with open(path, "w") as fh:
        fh.write("\n".join(lines))


def _fmt(value):
    return "" if value is None or (isinstance(value, float) and math.isnan(value)) else f"{value:.4f}"


if __name__ == "__main__":
    main()
