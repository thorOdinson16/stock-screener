"""Opt-in Spark/Iceberg pipeline test: no live warehouse is used."""

from datetime import date, timedelta, datetime
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "spark/jobs"), str(ROOT / "ml/evaluation"), str(ROOT / "ml/feature_engineering")]


@unittest.skipUnless("--spark" in sys.argv, "requires spark-submit --spark")
def test_safe_training():
    from build_training import build_spark, build_dataset
    from compute_indicators import FEATURE_SCHEMA
    from indicators import compute_features
    from corporate_actions import load_events, reset_features
    from pyspark.sql import functions as F
    with tempfile.TemporaryDirectory(prefix="safe-training-") as temporary:
        spark = build_spark(Path(temporary).as_uri())
        spark.sparkContext.addPyFile(str(ROOT / "spark/jobs/indicators.py"))
        spark.sparkContext.setLogLevel("ERROR")
        try:
            days = []
            day = date(2025, 9, 1)
            while len(days) < 260:
                if day.weekday() < 5:
                    days.append(day)
                day += timedelta(days=1)
            rows = [(s, day, (700. if day < date(2026, 4, 30) else 280.) if s == "VEDL.NS" else 100., 1000000)
                    for s in ["VEDL.NS", "A.NS", "^NSEI"] for day in days]
            raw = spark.createDataFrame(rows, "symbol string, trade_date date, close double, volume long")
            events = load_events()
            def compute(pdf):
                return reset_features(pdf, events, compute_features)
            safe = raw.groupBy("symbol").applyInPandas(compute, FEATURE_SCHEMA).cache()
            data, _, _ = build_dataset(spark, silver_frame=safe)
            impacted = data.filter((F.col("symbol") == "VEDL.NS") & (F.col("trade_date") == F.lit("2026-04-29").cast("date"))).first()
            assert impacted.fwd_ret_5d is None and impacted.fwd_ret_21d is None
            peer = data.filter((F.col("symbol") == "A.NS") & (F.col("trade_date") == F.lit("2026-04-29").cast("date"))).first()
            assert peer.fwd_ret_5d == 0. and peer.excess_ret_5d is None
            post = safe.filter((F.col("symbol") == "VEDL.NS") & (F.col("trade_date") == F.lit("2026-05-04").cast("date"))).first()
            assert post.close == 280. and post.sma_200 is None and post.ema_12 == 280.
            assert data.count() == len(rows)
            # A stock excluded from today's scoring must not retain yesterday's
            # rank in the newly published screener snapshot.
            from publish_screener import latest_score
            spark.sql("CREATE NAMESPACE iceberg.gold")
            spark.sql("CREATE TABLE iceberg.gold.stock_scores (symbol STRING, trade_date DATE, label STRING, score DOUBLE, rank INT, scored_at TIMESTAMP) USING iceberg")
            score_rows = [("A.NS", date(2026, 6, 1), "excess_ret_21d", .2, 1, datetime(2026, 6, 1)),
                          ("VEDL.NS", date(2026, 4, 29), "excess_ret_21d", .3, 1, datetime(2026, 4, 29))]
            spark.createDataFrame(score_rows, "symbol string, trade_date date, label string, score double, rank int, scored_at timestamp").writeTo("iceberg.gold.stock_scores").append()
            scores = latest_score(spark, "excess_ret_21d", "score_21d", "rank_21d").collect()
            assert len(scores) == 1 and scores[0].symbol == "A.NS" and scores[0].trade_date == "2026-06-01"
            print("PASS: Spark resets structural features, masks entitlement labels, and withholds incomplete universe targets")
            print("PASS: Published scores cannot retain a stale cross-section rank")
        finally:
            spark.stop()


if __name__ == "__main__":
    test_safe_training()
