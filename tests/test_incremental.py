"""Run with python; add --spark under spark-submit to test Iceberg writes."""

from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "poller"))
from incremental import history_start


def test_per_symbol_progress():
    progress = {"A.NS": "2026-11-06", "B.NS": "2026-10-23"}
    assert history_start("A.NS", progress) == "2026-11-01"
    assert history_start("B.NS", progress) == "2026-10-18"
    assert history_start("NEW.NS", progress) is None
    assert history_start("A.NS", progress, 0) == "2026-11-06"
    assert history_start("A.NS", progress, 10) == "2026-10-27"
    assert history_start("NEW.NS", {}) is None
    try:
        history_start("A.NS", progress, -1)
    except ValueError:
        pass
    else:
        raise AssertionError("negative overlap accepted")


@unittest.skipUnless("--spark" in sys.argv, "requires spark-submit --spark")
def test_iceberg_overlap_and_retry():
    from datetime import date, datetime
    from pyspark.sql import SparkSession
    sys.path.insert(0, str(ROOT / "spark" / "jobs"))
    from daily_history import latest_bars
    from nullability import match_not_null

    with tempfile.TemporaryDirectory(prefix="incremental-test-") as warehouse:
        spark = (SparkSession.builder.master("local[2]").appName("test_incremental")
                 .config("spark.ui.enabled", "false")
                 .config("spark.sql.shuffle.partitions", "2")
                 .config("spark.sql.extensions", "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions")
                 .config("spark.sql.catalog.iceberg", "org.apache.iceberg.spark.SparkCatalog")
                 .config("spark.sql.catalog.iceberg.type", "hadoop")
                 .config("spark.sql.catalog.iceberg.warehouse", Path(warehouse).as_uri()).getOrCreate())
        spark.sparkContext.setLogLevel("ERROR")
        try:
            spark.sql("CREATE NAMESPACE iceberg.bronze")
            table = "iceberg.bronze.quotes_daily"
            spark.sql(f"CREATE TABLE {table} (symbol STRING NOT NULL, trade_date DATE NOT NULL, close DOUBLE, ingested_at TIMESTAMP NOT NULL) USING iceberg")
            rows = [
                ("A", date(2026, 10, 1), 10., datetime(2026, 10, 1)),
                ("A", date(2026, 11, 1), 20., datetime(2026, 11, 1)),
                ("B", date(2026, 10, 23), 30., datetime(2026, 10, 23)),
                ("A", date(2026, 11, 1), 21., datetime(2026, 11, 2)),
                ("A", date(2026, 11, 6), 22., datetime(2026, 11, 6)),
            ]
            frame = spark.createDataFrame(rows, "symbol string, trade_date date, close double, ingested_at timestamp")
            match_not_null(frame, spark, table).writeTo(table).append()
            for _ in range(2):
                match_not_null(latest_bars(spark.table(table)), spark, table).createOrReplaceTempView("daily_history_latest")
                spark.sql(f"INSERT OVERWRITE {table} SELECT * FROM daily_history_latest")
            result = spark.table(table).collect()
            assert len(result) == 4
            values = {(r.symbol, r.trade_date): r.close for r in result}
            assert values[("A", date(2026, 11, 1))] == 21.
            assert values[("A", date(2026, 10, 1))] == 10.
            assert values[("B", date(2026, 10, 23))] == 30.
            progress = {r.symbol: r.latest.isoformat() for r in spark.table(table)
                        .groupBy("symbol").agg({"trade_date": "max"})
                        .withColumnRenamed("max(trade_date)", "latest").collect()}
            assert history_start("B", progress) == "2026-10-18"
            assert history_start("A", progress) == "2026-11-01"
        finally:
            spark.stop()


if __name__ == "__main__":
    test_per_symbol_progress()
    print("PASS per-symbol progress, new symbols, overlap boundaries")
    if "--spark" in sys.argv:
        test_iceberg_overlap_and_retry()
        print("PASS Iceberg overlap replacement, historical retention, retry, watermarks")
