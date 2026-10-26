"""
research.py — walk-forward comparison of candidate feature sets and horizons.

The production evaluation (evaluate.py) picks a model on the fixed test year and
then walk-forwards only that pick. To decide whether new inputs help, this script
instead runs the same expanding-window walk-forward (walk_forward.py: 5 folds,
21-day embargo) for fixed model specs on several feature sets, so the comparison
isn't confounded by the model-selection step.

Feature sets:
  base  the technical features (transform.TECHNICAL_FEATURES; production before
        fundamentals were adopted)
  tech  base + time-series and sector-relative features (extra_features.py)
  fund  base + point-in-time fundamentals from annual statements
  all   base + tech + fund

Run (stack up; from the repo root):
    spark-submit --driver-memory 8g \
      --packages org.apache.iceberg:iceberg-spark-runtime-3.3_2.12:1.8.1 \
      ml/evaluation/research.py --models linear --labels excess_ret_21d

Writes ml/experiments/<ts>_research.json and ml/evaluation/results/research.md.
"""

import argparse
import json
import math
import os
import sys
import tempfile

import numpy as np
import pandas as pd

from pyspark.ml.feature import VectorAssembler
from pyspark.sql import SparkSession, Window
from pyspark.sql import functions as F

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
_FEATURES_DIR = os.path.join(_REPO_ROOT, "ml", "feature_engineering")
for sub in ("feature_engineering", "evaluation", "experiments", "training"):
    sys.path.insert(0, os.path.join(_REPO_ROOT, "ml", sub))

from backtest import backtest  # noqa: E402
from extra_features import (  # noqa: E402
    FUNDAMENTAL_FEATURES,
    RELATIVE_FEATURES,
    TIMESERIES_FEATURES,
    add_fundamental_features,
    add_relative_features,
    add_timeseries_features,
)
from features import BENCHMARK_SYMBOLS  # noqa: E402
from metrics import information_coefficient  # noqa: E402
from registry import record as record_experiment  # noqa: E402
from train_model import estimator  # noqa: E402
from transform import (  # noqa: E402
    RAW_FEATURE_COLUMNS,
    TECHNICAL_FEATURES as MODEL_FEATURES,
    add_derived_features,
    apply_cross_sectional,
)
from walk_forward import expanding_folds  # noqa: E402

UNIVERSE_CACHE = os.path.join(_REPO_ROOT, "poller", "universe", "universe_cache.json")
HORIZONS = (5, 21, 63)
EXTRA_FEATURES = TIMESERIES_FEATURES + RELATIVE_FEATURES + FUNDAMENTAL_FEATURES
FEATURE_SETS = {
    "base": list(MODEL_FEATURES),
    "tech": list(MODEL_FEATURES) + TIMESERIES_FEATURES + RELATIVE_FEATURES,
    "fund": list(MODEL_FEATURES) + FUNDAMENTAL_FEATURES,
    "all": list(MODEL_FEATURES) + EXTRA_FEATURES,
}
# Fixed specs (mid-grid values from train_model.GRIDS) so feature sets are
# compared like for like. `rank_ensemble` averages the two rank models, as in
# production.
MODEL_SPECS = {
    "gbt_rank": {"maxIter": 50, "maxDepth": 4},
    "rf_rank": {"numTrees": 60, "maxDepth": 6},
    "linear": {"regParam": 0.1},
}
DEFAULT_K = 20
DEFAULT_COST_BPS = 10.0


def build_spark(warehouse: str) -> SparkSession:
    spark = (
        SparkSession.builder.appName("research")
        .config(
            "spark.sql.extensions",
            "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions",
        )
        .config("spark.sql.catalog.iceberg", "org.apache.iceberg.spark.SparkCatalog")
        .config("spark.sql.catalog.iceberg.type", "hadoop")
        .config("spark.sql.catalog.iceberg.warehouse", warehouse)
        .config("spark.sql.defaultCatalog", "iceberg")
        .config("spark.sql.session.timeZone", "UTC")
        .config("spark.sql.execution.arrow.pyspark.enabled", "true")
        .config("spark.kryoserializer.buffer.max", "1g")
        .config("spark.driver.maxResultSize", "4g")
        .getOrCreate()
    )
    return spark


def load_panel(spark: SparkSession) -> pd.DataFrame:
    """Silver indicators + extra features + labels, as one pandas panel."""
    silver = spark.table("iceberg.silver.quotes_enriched").select(
        "symbol", "trade_date",
        F.col("close").cast("double").alias("close"),
        F.col("volume").cast("double").alias("volume"),
        *[F.col(c).cast("double").alias(c) for c in RAW_FEATURE_COLUMNS],
    ).dropna(subset=["close"])
    panel = silver.toPandas()
    panel["trade_date"] = pd.to_datetime(panel["trade_date"])

    index = panel[panel["symbol"].isin(BENCHMARK_SYMBOLS)].sort_values("trade_date")
    index_returns = index.set_index("trade_date")["close"].pct_change()

    with open(UNIVERSE_CACHE) as fh:
        industries = {s["yf_symbol"]: s.get("industry") or "Unknown"
                      for s in json.load(fh)["stocks"]}
    panel["industry"] = panel["symbol"].map(industries).fillna("Unknown")

    # Forward returns (raw) per symbol, index included for the benchmark.
    panel = panel.sort_values(["symbol", "trade_date"]).reset_index(drop=True)
    for h in HORIZONS:
        future = panel.groupby("symbol", sort=False)["close"].shift(-h)
        panel[f"fwd_ret_{h}d"] = future / panel["close"] - 1.0
    index_fwd = panel[panel["symbol"].isin(BENCHMARK_SYMBOLS)].set_index("trade_date")
    panel = panel[~panel["symbol"].isin(BENCHMARK_SYMBOLS)].copy()
    for h in HORIZONS:
        raw = f"fwd_ret_{h}d"
        panel[f"universe_ret_{h}d"] = panel.groupby("trade_date")[raw].transform("mean")
        panel[f"excess_ret_{h}d"] = panel[raw] - panel[f"universe_ret_{h}d"]
        panel[f"index_ret_{h}d"] = panel["trade_date"].map(index_fwd[raw])

    # Time-series extras, per symbol.
    panel = panel.groupby("symbol", group_keys=False).apply(
        lambda g: add_timeseries_features(g, index_returns)
    )

    # Fundamentals (as-of with publication lag), if statements exist.
    try:
        stmts = spark.table("iceberg.silver.financial_statements").toPandas()
    except Exception:  # noqa: BLE001
        stmts = pd.DataFrame()
    if stmts.empty:
        print("WARNING: no silver.financial_statements — fundamental features are empty")
        for c in FUNDAMENTAL_FEATURES:
            panel[c] = np.nan
    else:
        panel = add_fundamental_features(panel, stmts)

    panel = add_derived_features(panel)
    panel = panel.groupby("trade_date", group_keys=False).apply(add_relative_features)

    coverage = {c: float(panel[c].notna().mean()) for c in EXTRA_FEATURES}
    panel = apply_cross_sectional(panel, columns=list(MODEL_FEATURES) + EXTRA_FEATURES)
    # Extras are neutral (0) where unavailable (warm-up, pre-statement history),
    # so rows aren't dropped; the base features keep production's drop behaviour.
    panel[EXTRA_FEATURES] = panel[EXTRA_FEATURES].fillna(0.0)
    panel["trade_date"] = panel["trade_date"].dt.date
    return panel.dropna(subset=list(MODEL_FEATURES)).reset_index(drop=True), coverage


def walk_forward(sdf, dates, features, label, models, k, cost_bps):
    horizon = int(label.split("_")[-1].rstrip("d"))
    raw = f"fwd_ret_{horizon}d"
    universe_col, index_col = f"universe_ret_{horizon}d", f"index_ret_{horizon}d"
    assembler = VectorAssembler(inputCols=features, outputCol="features", handleInvalid="skip")
    rank_window = Window.partitionBy("trade_date").orderBy(label)

    labelled = sdf.filter(F.col(label).isNotNull())
    frames, per_fold = [], []
    for i, fold in enumerate(expanding_folds(dates)):
        train = labelled.filter(F.col("trade_date") <= F.lit(fold["train_dates"][-1]))
        test = labelled.filter(
            (F.col("trade_date") >= F.lit(fold["test_dates"][0]))
            & (F.col("trade_date") <= F.lit(fold["test_dates"][-1]))
        )
        train = train.withColumn("rank_target", F.percent_rank().over(rank_window) - 0.5)
        test_vec = assembler.transform(test.select(*features, "symbol", "trade_date"))
        preds = None
        for name in models:
            target = "rank_target" if name.endswith("_rank") else label
            fitted = estimator(name, dict(MODEL_SPECS[name])).fit(
                assembler.transform(train.select(*features, F.col(target).alias("label")))
            )
            p = fitted.transform(test_vec).select(
                "symbol", "trade_date", F.col("prediction").alias(f"p_{name}"))
            preds = p if preds is None else preds.join(p, ["symbol", "trade_date"])
        pcols = [f"p_{m}" for m in models]
        scored = preds.withColumn("score", sum(F.col(c) for c in pcols) / float(len(pcols)))
        pdf = (
            test.select("symbol", "trade_date", label, raw, universe_col, index_col)
            .join(scored.select("symbol", "trade_date", "score"), ["symbol", "trade_date"])
            .toPandas()
        )
        ic = information_coefficient(pdf, "score", label, lags=horizon - 1)
        per_fold.append({"fold": i, "n": int(len(pdf)), "ic_mean": ic["ic_mean"],
                         "ic_t_stat": ic["ic_t_stat"]})
        frames.append(pdf)
        print(f"    fold {i}: IC={ic['ic_mean']:.4f} t={ic['ic_t_stat']:.2f}", flush=True)

    wf = pd.concat(frames, ignore_index=True)
    ic = information_coefficient(wf, "score", label, lags=horizon - 1)
    bt = backtest(wf, horizon, k=k, cost_bps=cost_bps, long_short=True, ret_col=raw,
                  score_col="score", universe_ret_col=universe_col, index_ret_col=index_col)
    fold_ics = [f["ic_mean"] for f in per_fold if f["ic_mean"] is not None
                and not math.isnan(f["ic_mean"])]
    return {
        "ic_mean": ic["ic_mean"],
        "ic_t_stat": ic["ic_t_stat"],
        "net_sharpe": bt.get("net_sharpe"),
        "folds_positive": sum(v > 0 for v in fold_ics) / len(fold_ics) if fold_ics else None,
        "per_fold": per_fold,
    }


MODEL_GROUPS = {
    "linear": ["linear"],
    "rank_ensemble": ["gbt_rank", "rf_rank"],
    "gbt_rank": ["gbt_rank"],
}


def main():
    parser = argparse.ArgumentParser(description="Walk-forward feature-set research")
    parser.add_argument("--warehouse", default="hdfs://localhost:9000/warehouse")
    parser.add_argument("--sets", default="base,tech,fund,all")
    parser.add_argument("--labels", default="excess_ret_5d,excess_ret_21d,excess_ret_63d")
    parser.add_argument("--models", default="linear,rank_ensemble")
    parser.add_argument("--k", type=int, default=DEFAULT_K)
    parser.add_argument("--cost-bps", type=float, default=DEFAULT_COST_BPS)
    args = parser.parse_args()

    spark = build_spark(args.warehouse)
    spark.sparkContext.setLogLevel("WARN")
    panel, coverage = load_panel(spark)
    print(f"panel rows={len(panel)} symbols={panel['symbol'].nunique()} "
          f"dates={panel['trade_date'].nunique()}")
    print("extra-feature coverage (share of rows with a value before neutral fill):")
    for c, v in coverage.items():
        print(f"  {c}: {v:.1%}")

    keep = (["symbol", "trade_date"] + list(MODEL_FEATURES) + EXTRA_FEATURES
            + [f"{p}_{h}d" for h in HORIZONS
               for p in ("fwd_ret", "excess_ret", "universe_ret", "index_ret")])
    # Hand the panel to Spark via Parquet: Spark 3.3's createDataFrame(pandas)
    # calls DataFrame.iteritems, which pandas 2 removed.
    panel_path = os.path.join(tempfile.mkdtemp(prefix="research-"), "panel.parquet")
    panel[keep].to_parquet(panel_path, index=False)
    sdf = spark.read.parquet(f"file://{panel_path}").repartition(16).cache()
    dates = sorted(panel["trade_date"].unique())

    results = []
    for label in args.labels.split(","):
        for group in args.models.split(","):
            for set_name in args.sets.split(","):
                print(f"[{label}] {group} on {set_name} ...", flush=True)
                r = walk_forward(sdf, dates, FEATURE_SETS[set_name], label,
                                 MODEL_GROUPS[group], args.k, args.cost_bps)
                r.update({"label": label, "model": group, "feature_set": set_name,
                          "n_features": len(FEATURE_SETS[set_name])})
                results.append(r)
                print(f"  -> IC={r['ic_mean']:.4f} t={r['ic_t_stat']:.2f} "
                      f"Sharpe={r['net_sharpe']} folds+={r['folds_positive']}", flush=True)

    record_experiment(
        "research",
        {"k": args.k, "cost_bps": args.cost_bps, "statement_coverage": coverage,
         "feature_sets": FEATURE_SETS, "model_specs": MODEL_SPECS, "results": results},
        _REPO_ROOT, os.path.join(_REPO_ROOT, "ml", "experiments"),
    )
    lines = ["# Walk-forward feature research", "",
             "| label | model | features | IC | IC t | net Sharpe | folds + | fold ICs |",
             "|---|---|---|---|---|---|---|---|"]
    for r in results:
        folds = ", ".join(f"{f['ic_mean']:+.3f}" for f in r["per_fold"])
        lines.append(f"| {r['label']} | {r['model']} | {r['feature_set']} | "
                     f"{r['ic_mean']:.4f} | {r['ic_t_stat']:.2f} | {r['net_sharpe']:.2f} | "
                     f"{r['folds_positive']:.0%} | {folds} |")
    out = os.path.join(_HERE, "results", "research.md")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    spark.stop()


if __name__ == "__main__":
    main()
