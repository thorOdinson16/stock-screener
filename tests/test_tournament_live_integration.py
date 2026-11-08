"""Opt-in isolated Spark export parity test; never uses the live warehouse."""

from datetime import date, datetime, timedelta
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "spark/jobs"), str(ROOT / "ml/feature_engineering")]


@unittest.skipUnless("--spark" in sys.argv, "requires spark-submit --spark")
def test_paper_feature_parity():
    import json
    import math
    import numpy as np
    import pandas as pd
    from build_training import build_spark
    from compute_indicators import FEATURE_SCHEMA
    from indicators import compute_features
    from transform import transform_group, TECHNICAL_FEATURES
    from ml.evaluation.corporate_actions import load_events, fingerprint
    import export_tournament_live
    with tempfile.TemporaryDirectory(prefix="tournament-live-") as directory:
        base = Path(directory)
        warehouse = (base / "warehouse").as_uri()
        spark = build_spark(warehouse)
        spark.sparkContext.setLogLevel("ERROR")
        try:
            days, day = [], date.today() - timedelta(days=1)
            while len(days) < 260:
                if day.weekday() < 5:
                    days.append(day)
                day -= timedelta(days=1)
            days.sort()
            rows = []
            for s, factor in [("TEST_A.NS", 1.), ("TEST_B.NS", 1.8), ("^NSEI", 100.)]:
                for i, day in enumerate(days):
                    close = factor * (100 + .1 * i + math.sin(i * .23))
                    rows.append((s, day, close, close * .99, close, 1000000, datetime.now()))
            raw = spark.createDataFrame(rows, "symbol string, trade_date date, open double, low double, close double, volume long, ingested_at timestamp")
            spark.sql("CREATE NAMESPACE iceberg.bronze")
            spark.sql("CREATE NAMESPACE iceberg.silver")
            raw.writeTo("iceberg.bronze.quotes_daily").using("iceberg").create()
            raw_pdf = raw.select("symbol", "trade_date", "close", "volume").toPandas()
            silver_pdf = pd.concat([compute_features(g) for _, g in raw_pdf.groupby("symbol")], ignore_index=True)
            feature_rows = [tuple(r[c] for c in FEATURE_SCHEMA.fieldNames()) for r in silver_pdf.to_dict("records")]
            spark.createDataFrame(feature_rows, FEATURE_SCHEMA).writeTo("iceberg.silver.quotes_enriched").using("iceberg").create()
            digest = fingerprint(load_events())
            spark.sql(f"ALTER TABLE iceberg.silver.quotes_enriched SET TBLPROPERTIES ('quality.policy.digest'='{digest}')")
            expected = transform_group(silver_pdf[silver_pdf.trade_date == days[-1]])
            previous = sys.argv
            try:
                sys.argv = ["export_tournament_live.py", "--warehouse", warehouse,
                            "--output", str(base / "output"), "--paper", str(base / "paper")]
                export_tournament_live.main()
            finally:
                sys.argv = previous
            actual = pd.read_parquet(base / "output/dataset.parquet")
            latest = actual[actual.trade_date == days[-1].isoformat()].set_index("symbol")
            for symbol in ["TEST_A.NS", "TEST_B.NS"]:
                target = expected[expected.symbol == symbol].iloc[0]
                np.testing.assert_allclose(latest.loc[symbol, TECHNICAL_FEATURES].to_numpy(dtype=float), target[TECHNICAL_FEATURES].to_numpy(dtype=float), atol=1e-12)
            bars = pd.read_parquet(base / "output/bars.parquet")
            assert bars[bars.symbol == "^NSEI"].trade_date.nunique() >= 220
            manifest = json.loads((base / "output/manifest.json").read_text())
            assert manifest["last_session"] == days[-1].isoformat()
            assert manifest["events_digest"] == digest
            assert "^NSEI" not in set(actual.symbol)
            print("PASS: Paper export matches training technical transforms, preserves benchmark normalization and supplies the 63-session control history")
        finally:
            spark.stop()


if __name__ == "__main__":
    test_paper_feature_parity()
