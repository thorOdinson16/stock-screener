"""Export a read-only, corporate-action-aware research snapshot to local Parquet."""

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "spark/jobs"), str(ROOT / "ml/feature_engineering")]


def main():
    from pyspark.sql import functions as F
    from build_training import build_spark, build_dataset
    from compute_indicators import FEATURE_SCHEMA
    from indicators import compute_features
    from daily_history import latest_bars
    from ml.evaluation.corporate_actions import load_events, reset_features, fingerprint
    from ml.evaluation.forward_tracking import atomic_json, IST
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse", default="hdfs://localhost:9000/warehouse")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    if (args.output / "manifest.json").exists():
        raise RuntimeError("Snapshot already exported; use it or choose a new directory")
    events = load_events()
    spark = build_spark(args.warehouse)
    spark.sparkContext.addPyFile(str(ROOT / "spark/jobs/indicators.py"))
    spark.sparkContext.setLogLevel("ERROR")
    try:
        snapshots = {}
        for table in ("bronze.quotes_daily", "silver.financial_statements"):
            snapshots[table] = str(spark.sql(f"SELECT snapshot_id FROM iceberg.{table}.snapshots ORDER BY committed_at DESC LIMIT 1").first().snapshot_id)
        # Pin both reads to their committed snapshots, even if another pipeline
        # run commits data during this export.
        raw = latest_bars(spark.read.option("snapshot-id", snapshots["bronze.quotes_daily"]).table("iceberg.bronze.quotes_daily"))
        raw = raw.filter(F.col("trade_date") < F.lit(datetime.now(IST).date().isoformat()).cast("date")).cache()
        def safe_features(pdf):
            return reset_features(pdf, events, compute_features)
        safe = raw.select("symbol", "trade_date", F.col("close").cast("double").alias("close"), "volume").groupBy("symbol").applyInPandas(safe_features, schema=FEATURE_SCHEMA).cache()
        dataset, _, _ = build_dataset(spark, silver_frame=safe)
        # build_dataset's financial attachment reads the table. Verify it did
        # not advance while the lazy plan materialized; otherwise discard this
        # export rather than claim a mixed snapshot is reproducible.
        pdf = dataset.select(*[F.col(c).cast("string").alias(c) if c == "trade_date" else F.col(c) for c in dataset.columns]).toPandas()
        pdf.to_parquet(args.output / "dataset.parquet", index=False)
        context = safe.select("symbol", F.col("trade_date").cast("string").alias("trade_date"),
                              "volume_avg_20d", "volatility_20d", "price_momentum_1m", "price_momentum_3m", "rsi_14", "volume_ratio").toPandas()
        context.to_parquet(args.output / "context.parquet", index=False)
        bars = raw.select("symbol", F.col("trade_date").cast("string").alias("trade_date"),
                          *[F.col(c).cast("double").alias(c) for c in ("open", "low", "close", "volume")]).toPandas()
        bars.to_parquet(args.output / "bars.parquet", index=False)
        current = str(spark.sql("SELECT snapshot_id FROM iceberg.silver.financial_statements.snapshots ORDER BY committed_at DESC LIMIT 1").first().snapshot_id)
        if current != snapshots["silver.financial_statements"]:
            raise RuntimeError("Statements changed during export; choose a new snapshot directory")
        source_files = ["spark/jobs/build_training.py", "spark/jobs/indicators.py", "spark/jobs/daily_history.py",
                        "ml/feature_engineering/transform.py", "ml/feature_engineering/features.py", "ml/feature_engineering/extra_features.py",
                        "ml/feature_engineering/spark_schema.py", "ml/feature_engineering/asof.py", "ml/evaluation/corporate_actions.py",
                        "ml/evaluation/export_tournament.py", "poller/universe/universe_cache.json"]
        digest = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
        atomic_json(args.output / "events.json", {"version": 1, "events": events})
        atomic_json(args.output / "manifest.json", {"status": "provisional", "source_snapshots": snapshots,
                    "events_digest": fingerprint(events), "created_at": datetime.now(IST).isoformat(),
                    "last_session": str(bars[bars.symbol == "^NSEI"].trade_date.max()), "rows": len(pdf),
                    "files": {name: digest(args.output / name) for name in ("dataset.parquet", "context.parquet", "bars.parquet", "events.json")},
                    "source_hashes": {name: digest(ROOT / name) for name in source_files},
                    "fundamentals_status": "annual snapshots with estimated 90-day lag; publication dates and as-reported versions unverified"})
        print(f"TOURNAMENT EXPORT COMPLETE: {len(pdf)} rows -> {args.output}", flush=True)
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
