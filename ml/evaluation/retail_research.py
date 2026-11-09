"""Controlled retail experiments from corporate-action-aware historical features.

Research only: temporary models, no production selection or table overwrite.
The earlier three folds select a candidate; the last two assess it. All results
remain provisional with adjusted Yahoo data and unresolved entitlement values.
"""

import argparse
from dataclasses import asdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "spark/jobs"), str(ROOT / "ml/feature_engineering"),
               str(ROOT / "ml/training"), str(ROOT / "ml/evaluation")]


def main():
    import pandas as pd
    from pyspark.sql import Window, functions as F
    from pyspark.ml.feature import VectorAssembler
    from build_training import build_spark, build_dataset
    from compute_indicators import FEATURE_SCHEMA
    from indicators import compute_features
    from daily_history import latest_bars
    from corporate_actions import load_events, reset_features, fingerprint
    from transform import MODEL_FEATURES
    from train_model import estimator
    from walk_forward import expanding_folds
    from forward_tracking import atomic_json
    from retail import Policy, simulate

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse", default="hdfs://localhost:9000/warehouse")
    parser.add_argument("--output", default=str(ROOT / "ml/evaluation/results/retail_research"))
    args = parser.parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    events = load_events()
    spark = build_spark(args.warehouse)
    spark.sparkContext.addPyFile(str(ROOT / "spark/jobs/indicators.py"))
    spark.sparkContext.setLogLevel("ERROR")
    try:
        raw = latest_bars(spark.table("iceberg.bronze.quotes_daily")).cache()
        def safe_features(pdf):
            return reset_features(pdf, events, compute_features)
        safe = raw.select("symbol", "trade_date", F.col("close").cast("double").alias("close"), "volume").groupBy("symbol").applyInPandas(safe_features, schema=FEATURE_SCHEMA).cache()
        data, _, _ = build_dataset(spark, silver_frame=safe)
        # 63-session labels use the common benchmark calendar, not each stock's
        # count of available rows, and never cross an unresolved action window.
        window = Window.partitionBy("symbol").orderBy("trade_date")
        long_labels = raw.select("symbol", "trade_date", F.col("close").cast("double").alias("close"))
        long_labels = long_labels.withColumn("end63", F.lead("trade_date", 63).over(window)).withColumn("ret63", F.lead("close", 63).over(window) / F.col("close") - 1)
        calendar = raw.filter(F.col("symbol") == "^NSEI").select("trade_date").distinct()
        calendar = calendar.withColumn("expected63", F.lead("trade_date", 63).over(Window.orderBy("trade_date")))
        long_labels = long_labels.join(calendar, "trade_date").withColumn("ret63", F.when(F.col("end63") == F.col("expected63"), F.col("ret63")))
        for event in events:
            blocked = F.col("symbol").isin(event["symbols"]) & (F.col("trade_date") <= F.lit(event["impact_end"]).cast("date")) & (F.col("end63") >= F.lit(event["impact_start"]).cast("date"))
            long_labels = long_labels.withColumn("ret63", F.when(~blocked, F.col("ret63")))
        long_labels = long_labels.filter(~F.col("symbol").startswith("^"))
        mean63 = long_labels.groupBy("trade_date").agg(F.when(F.count("ret63") == F.count(F.lit(1)), F.avg("ret63")).alias("mean63"))
        targets = long_labels.join(mean63, "trade_date").select("symbol", "trade_date", (F.col("ret63") - F.col("mean63")).alias("excess_ret_63d"), "end63")
        data = data.filter(~F.col("symbol").startswith("^")).join(targets, ["symbol", "trade_date"]).withColumn("sector_relative_3m", F.col("price_momentum_3m") - F.avg("price_momentum_3m").over(Window.partitionBy("trade_date", "industry"))).cache()
        dates = [r.trade_date for r in data.dropna(subset=MODEL_FEATURES).select("trade_date").distinct().orderBy("trade_date").collect()]
        folds = expanding_folds(dates, embargo_days=63)
        if len(folds) != 5:
            raise RuntimeError("Five folds are required for the fixed protocol")
        registry = json.loads((ROOT / "ml/models/registry.json").read_text())
        specs = {name: registry["labels"]["excess_ret_21d"][name]["params"] for name in ("gbt_rank", "rf_rank")}
        variants = {"rank21": (21, MODEL_FEATURES), "sector21": (21, MODEL_FEATURES + ["sector_relative_3m"]), "rank63": (63, MODEL_FEATURES)}
        snapshots = {}
        for table in ["bronze.quotes_daily", "silver.financial_statements"]:
            r = spark.sql(f"SELECT snapshot_id FROM iceberg.{table}.snapshots ORDER BY committed_at DESC LIMIT 1").first()
            snapshots[table] = str(r.snapshot_id)
        config = {"source_snapshots": snapshots, "events_digest": fingerprint(events), "models": specs,
                  "variants": {k: {"horizon": v[0], "features": v[1]} for k, v in variants.items()},
                  "policy": asdict(Policy()), "embargo": 63, "validation_folds": [1, 2, 3], "assessment_folds": [4, 5],
                  "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  "selection": "Highest complete validation net return, at least 20 closed trades, maximum drawdown no worse than -25%; assessment never chooses the variant."}
        manifest = output / "config.json"
        if manifest.exists() and json.loads(manifest.read_text()) != config:
            raise RuntimeError("Research directory has a different configuration; choose a new output directory")
        atomic_json(manifest, config)
        # Keep the feature-policy evidence required by downstream calibration.
        atomic_json(output / "source_snapshots/config/corporate_actions.json", {"version": 1, "events": events})
        predictions = {}
        for name, (horizon, features) in variants.items():
            target = f"excess_ret_{horizon}d"
            frames = []
            assembler = VectorAssembler(inputCols=features, outputCol="features", handleInvalid="skip")
            for fold_id, fold in enumerate(folds, 1):
                print(f"RESEARCH {name} fold {fold_id}/5", flush=True)
                checkpoint = output / f"{name}_fold{fold_id}.csv"
                if checkpoint.exists():
                    frame = pd.read_csv(checkpoint, dtype={"trade_date": str})
                else:
                    train = data.filter(F.col("trade_date").isin(fold["train_dates"])).filter(F.col(target).isNotNull())
                    train = train.withColumn("label", F.percent_rank().over(Window.partitionBy("trade_date").orderBy(target)) - .5)
                    train = assembler.transform(train).select("features", "label").cache()
                    train.count()
                    test = assembler.transform(data.filter(F.col("trade_date").isin(fold["test_dates"]))).select("symbol", "trade_date", "features").cache()
                    members = []
                    for algo, params in specs.items():
                        print(f"FIT {name} fold {fold_id} {algo}", flush=True)
                        model = estimator(algo, dict(params)).fit(train)
                        members.append(model.transform(test).select("symbol", "trade_date", F.col("prediction").alias(algo)))
                    merged = members[0].join(members[1], ["symbol", "trade_date"])
                    frame = merged.select("symbol", "trade_date", ((F.col("gbt_rank") + F.col("rf_rank")) / 2).alias("score")).toPandas()
                    frame["trade_date"] = frame["trade_date"].astype(str)
                    frame.to_csv(checkpoint, index=False)
                    train.unpersist(); test.unpersist()
                frame["fold"] = fold_id
                frames.append(frame)
                atomic_json(output / "progress.json", {"variant": name, "fold": fold_id})
            predictions[name] = pd.concat(frames, ignore_index=True)
        print("RESEARCH fitting complete; constructing finite-capital portfolios", flush=True)
        bar_rows = raw.select("symbol", "trade_date", "open", "high", "low", "close", "volume").collect()
        bars = {(r.symbol, str(r.trade_date)): {key: float(r[key]) if r[key] is not None else None for key in ["open", "high", "low", "close", "volume"]} for r in bar_rows}
        sessions = sorted(day for symbol, day in bars if symbol == "^NSEI")
        liquidity = safe.select("symbol", "trade_date", "close", "volume_avg_20d", "volatility_20d", "price_momentum_1m", "price_momentum_3m", "rsi_14").toPandas()
        liquidity["trade_date"] = liquidity["trade_date"].astype(str)
        universe = json.loads((ROOT / "poller/universe/universe_cache.json").read_text())["stocks"]
        industries = {s["yf_symbol"]: s["industry"] for s in universe}
        results = {}
        boundaries = {"validation": (str(folds[0]["test_dates"][0]), str(folds[2]["test_dates"][-1])),
                      "assessment": (str(folds[3]["test_dates"][0]), sessions[-1])}
        for name, prediction in predictions.items():
            horizon = variants[name][0]
            frame = prediction.merge(liquidity, on=["symbol", "trade_date"], how="left")
            signals = {}
            for day, group in frame.groupby("trade_date", sort=True):
                ranked = group.sort_values(["score", "symbol"], ascending=[False, True]).head(20)
                signals[day] = [{**{k: (None if pd.isna(v) else v) for k, v in row.items()},
                                 "rank": rank, "industry": industries.get(row["symbol"], "Unknown")}
                                for rank, row in enumerate(ranked.to_dict("records"), 1)]
            results[name] = {}
            for period, (start, end) in boundaries.items():
                result = simulate(signals, bars, sessions, Policy(horizon=horizon), events, start, end)
                results[name][period] = result
                atomic_json(output / f"{name}_{period}.json", result)
        eligible = {name: r["validation"] for name, r in results.items() if r["validation"]["status"] == "complete" and
                    len(r["validation"]["trades"]) >= 20 and r["validation"]["max_drawdown"] >= -.25}
        winner = max(eligible, key=lambda name: eligible[name]["net_return"]) if eligible else None
        def metrics(r):
            return {k: r[k] for k in ["status", "blocked", "net_return", "max_drawdown", "trade_win_rate"]} | {"closed_trades": len(r["trades"])}
        report = {"status": "provisional", "production_changed": False, "selected_on_validation": winner,
                  "config": config, "periods": boundaries,
                  "results": {name: {period: metrics(r) for period, r in periods.items()} for name, periods in results.items()},
                  "promotion_allowed": False,
                  "limitations": ["Adjusted prices are not raw executable historical quotes; quantities and fills are diagnostic.",
                                  "Complex corporate actions stop a held-position valuation; no entitlements are guessed.",
                                  "Historical universe membership and statements remain subject to survivorship/restatement bias.",
                                  "The history was researched previously, so assessment is not a new untouched test.",
                                  "No model probability, target-price forecast, or predictive improvement is asserted."]}
        atomic_json(output / "report.json", report)
        def pct(v):
            return "unavailable" if v is None else f"{v * 100:.2f}%"
        lines = ["# Retail research — provisional", "", "Temporary models; no production promotion. Fees 10 bps/side and slippage 10 bps/side.", "",
                 "| Variant | Period | Status | Net return | Daily max drawdown | Closed trades |", "|---|---|---|---:|---:|---:|"]
        for name, periods in report["results"].items():
            for period, r in periods.items():
                lines.append(f"| {name} | {period} | {r['status']} | {pct(r['net_return'])} | {pct(r['max_drawdown'])} | {r['closed_trades']} |")
        lines += ["", f"Validation choice: {winner or 'none qualified'}. Assessment does not choose or promote a model.", ""] + [f"- {v}" for v in report["limitations"]]
        (output / "summary.md").write_text("\n".join(lines))
        print(f"RESEARCH COMPLETE: {output / 'summary.md'}", flush=True)
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
