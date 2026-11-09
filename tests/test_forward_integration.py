"""Opt-in local Spark/Iceberg integration; Druid responses are fixtures.

spark-submit --jars <iceberg-runtime.jar> tests/test_forward_integration.py --spark
"""

from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "spark" / "jobs"))
sys.path.insert(0, str(ROOT / "ml" / "evaluation"))


@unittest.skipUnless("--spark" in sys.argv, "requires spark-submit --spark")
def test_tracking_job():
    from pyspark.sql import SparkSession
    import track_forward
    from forward_tracking import IST, atomic_json, capture, read_report

    with tempfile.TemporaryDirectory(prefix="forward-integration-") as temporary:
        warehouse = (Path(temporary) / "warehouse").as_uri()
        output = Path(temporary) / "forward"
        spark = (SparkSession.builder.master("local[2]").appName("forward-test")
                 .config("spark.ui.enabled", "false")
                 .config("spark.sql.shuffle.partitions", "2")
                 .config("spark.sql.catalog.iceberg", "org.apache.iceberg.spark.SparkCatalog")
                 .config("spark.sql.catalog.iceberg.type", "hadoop")
                 .config("spark.sql.catalog.iceberg.warehouse", warehouse).getOrCreate())
        spark.sparkContext.setLogLevel("ERROR")
        try:
            spark.sql("CREATE NAMESPACE iceberg.bronze")
            spark.sql("CREATE TABLE iceberg.bronze.quotes_daily (symbol STRING, trade_date DATE, close DOUBLE, ingested_at TIMESTAMP) USING iceberg")
            now = datetime.now(timezone.utc)
            today = datetime.now(IST).date()
            sessions = [today - timedelta(days=d) for d in range(60, 0, -1) if (today - timedelta(days=d)).weekday() < 5]
            rows = [{"symbol": symbol, "rank_5d": rank, "rank_21d": rank, "score_5d": .1, "score_21d": .2}
                    for symbol, rank in [("A", 1), ("B", 2)]]
            old = datetime.combine(sessions[0] - timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
            capture(output, old.isoformat(), rows, {"version": "old"}, now=old)
            bars = [(symbol, day, 100. + i, now.replace(tzinfo=None))
                    for symbol in ["A", "B", "^NSEI"] for i, day in enumerate(sessions)]
            spark.createDataFrame(bars, "symbol string, trade_date date, close double, ingested_at timestamp").writeTo("iceberg.bronze.quotes_daily").append()
            expected = {"published_at": now.isoformat(), "count": 2, "model": {"version": "new"}}
            atomic_json(output / "expected.json", expected)
            def response(request, **kwargs):
                assert "TIME_PARSE" in json.loads(request.data)["query"]
                return io.BytesIO(json.dumps(rows).encode())
            with patch.object(track_forward, "DEFAULT_DIR", output), patch.object(track_forward.urllib.request, "urlopen", response), patch.object(sys, "argv", ["track_forward.py", "--warehouse", warehouse, "--output", str(output)]):
                track_forward.main()
            report = read_report(output)
            assert len(report["cohorts"]) == 4
            assert sum(r["status"] == "complete" for r in report["cohorts"]) == 2
            assert sum(r["status"] == "pending" for r in report["cohorts"]) == 2
            assert {r["model"]["version"] for r in report["cohorts"]} == {"old", "new"}
            print("PASS Spark job: served snapshot -> immutable picks -> Iceberg prices -> matured/pending report")
        finally:
            spark.stop()


if __name__ == "__main__":
    test_tracking_job()
