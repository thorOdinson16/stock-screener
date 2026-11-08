"""Walk-forward historical replay of the prospective Track Record methodology.

Reads existing Iceberg data; never writes models, training tables or live picks.
Prediction CSVs are checkpointed per fold. Historical model/feature selection
still has hindsight; this is not a replacement for prospective validation.
"""

import argparse
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "ml" / "feature_engineering"), str(ROOT / "ml" / "training"),
               str(ROOT / "ml" / "evaluation"), str(ROOT / "spark" / "jobs")]


def portfolio_statistics(cohorts):
    """One capital pool: select non-overlapping windows before inspecting outcomes."""
    scheduled, last_exit = [], ""
    for row in sorted(cohorts, key=lambda r: (r["entry_date"] or "9999", r["published_at"])):
        if row["entry_date"] and row["exit_date"] and row["entry_date"] >= last_exit:
            scheduled.append(row)
            last_exit = row["exit_date"]
    complete = [r for r in scheduled if r["status"] == "complete"]
    result = {"scheduled": len(scheduled), "complete": len(complete),
              "incomplete": len(scheduled) - len(complete),
              "mean_net_return": sum(r["net_return"] for r in complete) / len(complete) if complete else None,
              "total_return": None, "universe_total_return": None,
              "index_total_return": None, "max_drawdown": None, "curve": []}
    # Never turn missing constituents into an implicit cash position or skip a
    # scheduled loss. Withhold the capital curve if any scheduled cohort is incomplete.
    if not complete or len(complete) != len(scheduled):
        return result
    equity = universe = index = peak = 1.
    drawdown = 0.
    curve = [{"date": complete[0]["entry_date"], "equity": 1., "universe": 1., "index": 1.}]
    for row in complete:
        equity *= 1 + row["net_return"]
        universe *= 1 + row["universe_return"]
        index *= 1 + row["index_return"]
        peak = max(peak, equity)
        drawdown = min(drawdown, equity / peak - 1)
        curve.append({"date": row["exit_date"], "equity": equity, "universe": universe, "index": index})
    result.update(total_return=equity - 1, universe_total_return=universe - 1,
                  index_total_return=index - 1, max_drawdown=drawdown, curve=curve)
    return result


def write_markdown(report, output):
    def pct(value):
        return "unavailable" if value is None else f"{value * 100:.2f}%"
    lines = ["# Historical replay of published top-20 picks", "",
             "Walk-forward rank ensemble; next-session close entry; 10 bps per side; long only.", "",
             "This is a retrospective backtest, separate from prospective Track Record results.", "",
             "| Metric | 5 sessions | 21 sessions |", "|---|---:|---:|"]
    keys = [("Completed daily cohorts", "complete", False), ("Incomplete cohorts", "incomplete", False),
            ("Average net top-20 return", "mean_net_return", True),
            ("Average gross universe return", "mean_universe_return", True),
            ("Average gross NIFTY 50 return", "mean_index_return", True),
            ("Pick hit rate vs universe (gross)", "mean_hit_rate", True),
            ("Cohorts beating universe after costs", "cohort_win_rate", True),
            ("Worst cohort net return", "worst_net_return", True),
            ("Worst individual pick gross return", "worst_pick_return", True)]
    for title, key, percentage in keys:
        values = [report["summaries"][str(h)][key] for h in (5, 21)]
        lines.append(f"| {title} | " + " | ".join(pct(v) if percentage else str(v) for v in values) + " |")
    lines += ["", "Daily cohorts overlap. Their average return is not a cumulative return.", "",
              "## Non-overlapping schedule", "",
              "Windows are selected chronologically before checking completeness. Each window uses a fresh equal-weight allocation; gross benchmarks use the same windows.", ""]
    for horizon in (5, 21):
        p = report["portfolios"][str(horizon)]
        lines += [f"- **{horizon} sessions:** {p['complete']}/{p['scheduled']} complete; "
                  f"compounded net return {pct(p['total_return'])}; gross universe {pct(p['universe_total_return'])}; "
                  f"gross NIFTY 50 {pct(p['index_total_return'])}; exit-to-exit drawdown {pct(p['max_drawdown'])}."]
    lines += ["", "Drawdown is sampled at exits and can understate intraperiod losses. Cumulative returns are withheld if a scheduled cohort is incomplete.", "",
              "## Monthly consistency (entry month)", "", "| Horizon | Month | Complete cohorts | Mean net | Universe gross | NIFTY gross | Cohort win rate |", "|---|---|---:|---:|---:|---:|---:|"]
    for horizon in (5, 21):
        for month in report["summaries"][str(horizon)]["months"]:
            lines.append(f"| {horizon} | {month['month']} | {month['cohorts']} | {pct(month['net_return'])} | {pct(month['universe_return'])} | {pct(month['index_return'])} | {pct(month['win_rate'])} |")
    lines += ["", "## Scope and limitations", ""] + [f"- {c}" for c in report["caveats"]]
    lines += ["", "## Reproducibility", "", "```json", json.dumps(report["config"], indent=2), "```", ""]
    (output / "summary.md").write_text("\n".join(lines))


def main():
    import pandas as pd
    from pyspark.ml.feature import VectorAssembler
    from pyspark.sql import Window, functions as F
    from evaluate import build_spark, training_snapshot_id
    from train_model import estimator
    from transform import MODEL_FEATURES
    from walk_forward import expanding_folds
    from forward_tracking import IST, atomic_json, evaluate, summaries
    from daily_history import latest_bars

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse", default="hdfs://localhost:9000/warehouse")
    parser.add_argument("--output", default=str(ROOT / "ml/evaluation/results" / ("published_backtest_" + datetime.now().strftime("%Y%m%dT%H%M%S"))))
    args = parser.parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    registry = json.loads((ROOT / "ml/models/registry.json").read_text())
    specs = {label: {name: registry["labels"][label][name]["params"] for name in ("gbt_rank", "rf_rank")}
             for label in ("excess_ret_5d", "excess_ret_21d")}
    spark = build_spark(args.warehouse)
    spark.sparkContext.setLogLevel("ERROR")
    try:
        from corporate_actions import load_events, fingerprint
        recorded = spark.sql("SHOW TBLPROPERTIES iceberg.ml.training_dataset ('quality.policy.digest')").first()
        if recorded is None or recorded["value"] != fingerprint(load_events()):
            raise RuntimeError("Historical training data predates the quality policy. Rebuild via scripts/retrain.sh before running a new backtest.")
        dataset = spark.table("iceberg.ml.training_dataset").filter(~F.col("symbol").startswith("^")).cache()
        today = datetime.now(IST).date().isoformat()
        daily = latest_bars(spark.table("iceberg.bronze.quotes_daily")).filter(F.col("trade_date") < F.lit(today).cast("date"))
        prices = {(r.symbol, r.trade_date.isoformat()): float(r.close)
                  for r in daily.select("symbol", "trade_date", "close").collect() if r.close is not None and r.close > 0}
        sessions = sorted(d for symbol, d in prices if symbol == "^NSEI")
        if not sessions:
            raise RuntimeError("Benchmark index daily history is required")
        universe = defaultdict_list(dataset.select("trade_date", "symbol").collect())
        valid = dataset.dropna(subset=MODEL_FEATURES)
        dates = [r.trade_date for r in valid.select("trade_date").distinct().orderBy("trade_date").collect()]
        folds = expanding_folds(dates, embargo_days=21)
        if len(folds) != 5:
            raise RuntimeError(f"Expected five usable folds, found {len(folds)}")
        config = {"training_snapshot_id": training_snapshot_id(spark), "warehouse": args.warehouse,
                  "features": MODEL_FEATURES, "specifications": specs, "seed": 42, "k": 20,
                  "cost_bps": 10, "embargo_sessions": 21, "entry": "next-session-close",
                  "training_dates": [str(dates[0]), str(dates[-1])],
                  "source_hashes": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in [
                      "ml/evaluation/backtest_published.py", "ml/evaluation/forward_tracking.py", "ml/feature_engineering/transform.py"]}}
        prior = output / "config.json"
        if prior.exists() and json.loads(prior.read_text()) != config:
            raise RuntimeError("Output directory belongs to a different dataset/configuration; choose a new directory")
        atomic_json(prior, config)
        cohorts, fold_details = [], []
        assembler = VectorAssembler(inputCols=MODEL_FEATURES, outputCol="features", handleInvalid="skip")
        for horizon in (5, 21):
            label = f"excess_ret_{horizon}d"
            labeled = dataset.withColumn("_target_end", F.lead("trade_date", horizon).over(
                Window.partitionBy("symbol").orderBy("trade_date")))
            for number, fold in enumerate(folds, 1):
                checkpoint = output / f"predictions_{horizon}d_fold{number}.csv"
                detail = {"horizon": horizon, "fold": number, "train_start": str(fold["train_dates"][0]),
                          "train_end": str(fold["train_dates"][-1]), "test_start": str(fold["test_dates"][0]),
                          "test_end": str(fold["test_dates"][-1])}
                print(f"BACKTEST {horizon}d fold {number}/5: {detail}", flush=True)
                if checkpoint.exists():
                    prediction = pd.read_csv(checkpoint, dtype={"trade_date": str})
                else:
                    train = labeled.filter(F.col("trade_date").isin(fold["train_dates"])).filter(
                        F.col(label).isNotNull() & (F.col("_target_end") < F.lit(fold["test_dates"][0])))
                    train = train.withColumn("label", F.percent_rank().over(Window.partitionBy("trade_date").orderBy(label)) - .5)
                    train = assembler.transform(train).select("features", "label").cache()
                    train.count()
                    test = assembler.transform(dataset.filter(F.col("trade_date").isin(fold["test_dates"]))).select("symbol", "trade_date", "features").cache()
                    frames = []
                    for algo, params in specs[label].items():
                        print(f"FIT {horizon}d fold {number} {algo} {params}", flush=True)
                        model = estimator(algo, dict(params)).fit(train)
                        frames.append(model.transform(test).select("symbol", "trade_date", F.col("prediction").alias(algo)))
                    merged = frames[0].join(frames[1], ["symbol", "trade_date"])
                    prediction = merged.select("symbol", "trade_date", ((F.col("gbt_rank") + F.col("rf_rank")) / 2).alias("score")).toPandas()
                    prediction["trade_date"] = prediction["trade_date"].astype(str)
                    prediction.to_csv(checkpoint, index=False)
                    train.unpersist()
                    test.unpersist()
                detail["prediction_rows"] = len(prediction)
                fold_details.append(detail)
                for score_date, group in prediction.groupby("trade_date", sort=True):
                    top = group.sort_values(["score", "symbol"], ascending=[False, True]).head(20)
                    model = {"name": "rank_ensemble", "specs": specs[label], "fold": number, "trained_through": detail["train_end"]}
                    fingerprint = hashlib.sha256(json.dumps(model, sort_keys=True).encode()).hexdigest()
                    snapshot = {"id": f"historical-{horizon}-{score_date}", "published_at": f"{score_date}T16:00:00+05:30",
                                "publication_date": score_date, "universe": universe[score_date], "cost_bps": 10,
                                "model": model, "model_fingerprint": fingerprint,
                                "picks": {str(horizon): [{"symbol": r.symbol, "score": float(r.score), "rank": rank}
                                                        for rank, r in enumerate(top.itertuples(), 1)]}}
                    cohorts.append(evaluate(snapshot, horizon, prices, sessions))
                atomic_json(output / "progress.json", {"folds": fold_details, "cohorts": len(cohorts)})
        report = {"kind": "historical_walk_forward_replay", "config": config, "folds": fold_details,
                  "summaries": summaries(cohorts), "portfolios": {str(h): portfolio_statistics([r for r in cohorts if r["horizon"] == h]) for h in (5, 21)},
                  "cohorts": cohorts, "caveats": [
                      "Model weights use only earlier data, with a 21-session embargo. Features and model specifications were selected in earlier research on this same history; this is not an untouched test.",
                      "Today's NIFTY 500 membership creates survivorship bias; financial statements may be restated rather than as originally published.",
                      "Entry is the next completed session close, not the score-date close. This is a paper execution proxy using adjusted Yahoo prices.",
                      "Incomplete cohorts are reported explicitly and excluded from means; completion-filtered results can be biased.",
                      "Daily cohorts overlap. Non-overlapping portfolio curves, where available, use identical benchmark windows and exclude idle-period returns.",
                      "No live model, Iceberg table or prospective prediction record was modified."]}
        atomic_json(output / "report.json", report)
        write_markdown(report, output)
        flat = [{k: v for k, v in row.items() if k not in ("picks", "model")} for row in cohorts]
        pd.DataFrame(flat).to_csv(output / "cohorts.csv", index=False)
        picks = [{"cohort_id": row["id"], "horizon": row["horizon"], "entry_date": row["entry_date"], "exit_date": row["exit_date"], **p} for row in cohorts for p in row["picks"]]
        pd.DataFrame(picks).to_csv(output / "picks.csv", index=False)
        print(f"BACKTEST COMPLETE: {output / 'summary.md'}", flush=True)
    finally:
        spark.stop()


def defaultdict_list(rows):
    from collections import defaultdict
    output = defaultdict(list)
    for row in rows:
        output[str(row.trade_date)].append(row.symbol)
    return dict(output)


if __name__ == "__main__":
    main()
