"""Export recent shared price-only features for frozen paper inference.

Reads current action-aware indicators; writes local files only. No labels,
retraining, full-history indicator recomputation or production table changes.
"""

import argparse
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "spark/jobs"), str(ROOT / "ml/feature_engineering")]


def main():
    from pyspark.sql import functions as F
    from build_training import build_spark, universe_metadata
    from daily_history import latest_bars
    from transform import RAW_FEATURE_COLUMNS, transform_group
    from spark_schema import NORMALIZED_SCHEMA
    from ml.evaluation.corporate_actions import load_events, fingerprint
    from ml.evaluation.forward_tracking import atomic_json, IST
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse", default="hdfs://localhost:9000/warehouse")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--paper", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    if (args.output / "manifest.json").exists():
        raise RuntimeError("Live export exists; reuse it or choose a new output directory")
    spark = build_spark(args.warehouse)
    spark.sparkContext.setLogLevel("ERROR")
    try:
        events = load_events()
        today = datetime.now(IST).date().isoformat()
        silver_name = "iceberg.silver.quotes_enriched"
        recorded = spark.sql(f"SHOW TBLPROPERTIES {silver_name} ('quality.policy.digest')").first()
        if recorded is None or recorded["value"] != fingerprint(events):
            raise RuntimeError("Paper features require current action-aware indicators; run the normal pipeline first")
        snapshots = {}
        for table in ("bronze.quotes_daily", "silver.quotes_enriched"):
            snapshots[table] = str(spark.sql(f"SELECT snapshot_id FROM iceberg.{table}.snapshots ORDER BY committed_at DESC LIMIT 1").first().snapshot_id)
        daily = latest_bars(spark.read.option("snapshot-id", snapshots["bronze.quotes_daily"]).table("iceberg.bronze.quotes_daily"))
        daily = daily.filter(F.col("trade_date") < F.lit(today).cast("date"))
        latest = str(daily.filter(F.col("symbol") == "^NSEI").agg(F.max("trade_date")).first()[0])
        start = (datetime.fromisoformat(latest) - timedelta(days=65)).date().isoformat()
        silver = spark.read.option("snapshot-id", snapshots["silver.quotes_enriched"]).table(silver_name)
        base = silver.filter((F.col("trade_date") >= F.lit(start).cast("date")) &
                             (F.col("trade_date") <= F.lit(latest).cast("date"))).select("symbol", "trade_date", "close", *RAW_FEATURE_COLUMNS)
        # Training normalizes the full cross-section including benchmarks, then
        # removes benchmark rows. Preserve that exact order for paper inference.
        features = base.groupBy("trade_date").applyInPandas(transform_group, schema=NORMALIZED_SCHEMA)
        frame = features.join(base.select("symbol", "trade_date", "close"), ["symbol", "trade_date"]).join(universe_metadata(spark), "symbol", "left")
        frame = frame.filter(~F.col("symbol").startswith("^"))
        pdf = frame.select(*[F.col(c).cast("string").alias(c) if c == "trade_date" else F.col(c) for c in frame.columns]).toPandas()
        if pdf.empty or pdf.trade_date.max() != latest:
            raise RuntimeError("Indicators do not cover the latest completed benchmark session")
        pdf.to_parquet(args.output / "dataset.parquet", index=False)
        context = base.select("symbol", F.col("trade_date").cast("string").alias("trade_date"),
                              "volume_avg_20d", "volatility_20d", "price_momentum_1m", "price_momentum_3m", "rsi_14").toPandas()
        context.to_parquet(args.output / "context.parquet", index=False)
        published = list((args.paper / "predictions").glob("*.json"))
        # Context models additionally need a 200-session market trend for every
        # point of their twenty-session windows. Preserve a conservative buffer.
        momentum_start = (datetime.fromisoformat(latest) - timedelta(days=420)).date().isoformat()
        earliest = min([json.loads(p.read_text())["publication_date"] for p in published] + [momentum_start])
        bars = daily.filter(F.col("trade_date") >= F.lit(earliest).cast("date")).select(
            "symbol", F.col("trade_date").cast("string").alias("trade_date"),
            *[F.col(c).cast("double").alias(c) for c in ("open", "low", "close", "volume")]).toPandas()
        bars.to_parquet(args.output / "bars.parquet", index=False)
        atomic_json(args.output / "events.json", {"version": 1, "events": events})
        sources = ["spark/jobs/export_tournament_live.py", "spark/jobs/indicators.py", "spark/jobs/build_training.py", "spark/jobs/daily_history.py",
                   "ml/feature_engineering/transform.py", "ml/feature_engineering/spark_schema.py", "ml/feature_engineering/extra_features.py",
                   "ml/feature_engineering/features.py", "ml/feature_engineering/asof.py", "ml/evaluation/corporate_actions.py", "poller/universe/universe_cache.json"]
        digest = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
        atomic_json(args.output / "manifest.json", {"status": "paper_only", "source_snapshots": snapshots,
                    "events_digest": fingerprint(events), "last_session": latest, "created_at": datetime.now(IST).isoformat(),
                    "files": {name: digest(args.output / name) for name in ("dataset.parquet", "context.parquet", "bars.parquet", "events.json")},
                    "source_hashes": {name: digest(ROOT / name) for name in sources}, "rows": len(pdf),
                    "fundamentals_status": "Not used: paper-qualified candidates must use technical features only"})
        print(f"PAPER EXPORT COMPLETE: {latest}; {len(pdf)} recent feature rows", flush=True)
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
