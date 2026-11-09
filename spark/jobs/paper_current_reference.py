"""Freeze and score the current production model as a read-only paper control."""

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil
import sys
from urllib.parse import urlparse, unquote

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "spark/jobs"), str(ROOT / "ml/feature_engineering")]


def main():
    from pyspark.sql import functions as F
    from build_training import build_spark
    from indicators import compute_features
    from compute_indicators import FEATURE_SCHEMA
    from spark_schema import attach_fundamentals, NORMALIZED_SCHEMA
    from transform import RAW_FEATURE_COLUMNS, transform_group
    from score_stocks import scores_for_label
    from ml.evaluation.corporate_actions import load_events, reset_features
    from ml.evaluation.forward_tracking import atomic_json, IST
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--warehouse", default="hdfs://localhost:9000/warehouse")
    parser.add_argument("--use-silver", action="store_true", help="Reuse current production indicators when input is a matching live export")
    parser.add_argument("--freeze-dir", type=Path, default=ROOT / "ml/evaluation/results/remaining_plan_20261108/current_reference")
    args = parser.parse_args()
    args.freeze_dir.mkdir(parents=True, exist_ok=True)
    digest = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
    frozen_path = args.freeze_dir / "selected_21d.json"
    if not frozen_path.exists():
        selected = json.loads((ROOT / "ml/models/selected.json").read_text())
        meta = selected["labels"]["excess_ret_21d"]
        for i, member in enumerate(meta.get("members", [meta])):
            original = Path(unquote(urlparse(member["path"]).path))
            destination = args.freeze_dir / f"member_{i}"
            shutil.copytree(original, destination)
            member["original_path"] = member["path"]
            member["path"] = destination.resolve().as_uri()
        atomic_json(frozen_path, {"selected_at": selected["selected_at"], "metadata": meta,
                    "original_selected_sha256": digest(ROOT / "ml/models/selected.json"),
                    "files": {str(p.relative_to(args.freeze_dir)): digest(p) for p in args.freeze_dir.rglob("*") if p.is_file()},
                    "frozen_at": datetime.now(IST).isoformat()})
    frozen = json.loads(frozen_path.read_text())
    for name, expected in frozen["files"].items():
        if digest(args.freeze_dir / name) != expected:
            raise RuntimeError("Frozen current-model control changed")
    manifest = json.loads((args.input / "manifest.json").read_text())
    spark = build_spark(args.warehouse)
    spark.sparkContext.addPyFile(str(ROOT / "spark/jobs/indicators.py"))
    spark.sparkContext.setLogLevel("ERROR")
    try:
        events = load_events()
        raw = spark.read.parquet((args.input / "bars.parquet").resolve().as_uri()).select(
            "symbol", F.col("trade_date").cast("date").alias("trade_date"), F.col("close").cast("double").alias("close"), F.col("volume").cast("long").alias("volume"))
        def safe(pdf):
            return reset_features(pdf, events, compute_features)
        indicators = raw.groupBy("symbol").applyInPandas(safe, schema=FEATURE_SCHEMA)
        if args.use_silver:
            from ml.evaluation.corporate_actions import fingerprint
            recorded = spark.sql("SHOW TBLPROPERTIES iceberg.silver.quotes_enriched ('quality.policy.digest')").first()
            if recorded is None or recorded["value"] != fingerprint(events):
                raise RuntimeError("Current-model paper control requires current action-aware indicators")
            snapshot = manifest["source_snapshots"]["silver.quotes_enriched"]
            indicators = spark.read.option("snapshot-id", snapshot).table("iceberg.silver.quotes_enriched")
        # Reproduce the current serving chain, including its stock-only
        # normalization and legacy annual features; do not substitute new inputs.
        latest = indicators.filter((F.col("trade_date") == F.lit(manifest["last_session"]).cast("date")) & ~F.col("symbol").startswith("^"))
        base = latest.select("symbol", "trade_date", "close", *RAW_FEATURE_COLUMNS)
        financial_before = str(spark.sql("SELECT snapshot_id FROM iceberg.silver.financial_statements.snapshots ORDER BY committed_at DESC LIMIT 1").first().snapshot_id)
        enriched = attach_fundamentals(spark, base)
        features = enriched.groupBy("trade_date").applyInPandas(transform_group, schema=NORMALIZED_SCHEMA)
        scores = scores_for_label(features, frozen["metadata"], "excess_ret_21d").select("symbol", "score").collect()
        statements_snapshot = str(spark.sql("SELECT snapshot_id FROM iceberg.silver.financial_statements.snapshots ORDER BY committed_at DESC LIMIT 1").first().snapshot_id)
        if financial_before != statements_snapshot:
            raise RuntimeError("Reference statements changed during inference; no paired capture")
        atomic_json(args.output, {"status": "paper_reference_only", "trade_date": manifest["last_session"],
                    "scored_at": datetime.now(IST).isoformat(), "input_manifest": digest(args.input / "manifest.json"),
                    "model_sha256": digest(frozen_path), "financial_snapshot": statements_snapshot,
                    "scoring_source_sha256": digest(Path(__file__)),
                    "rows": [{"symbol": r.symbol, "score": float(r.score)} for r in scores],
                    "production_changed": False, "limits": ["Legacy fundamentals and adjusted-price execution remain provisional.",
                                                           "This control uses existing trained weights; no model selection or production tables are changed."]})
        print(f"CURRENT PAPER CONTROL: {len(scores)} fresh scores, no production writes", flush=True)
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
