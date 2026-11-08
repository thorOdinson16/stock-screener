"""Capture the fresh Druid top picks and evaluate previous prospective cohorts."""

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ml" / "evaluation"))
from forward_tracking import DEFAULT_DIR, IST, build_report, capture
from pyspark.sql import SparkSession


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse", default="hdfs://localhost:9000/warehouse")
    parser.add_argument("--druid-url", default="http://localhost:8888")
    parser.add_argument("--output", default=str(DEFAULT_DIR))
    args = parser.parse_args()

    def query(sql):
        request = urllib.request.Request(args.druid_url.rstrip("/") + "/druid/v2/sql",
                                         data=json.dumps({"query": sql}).encode(),
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)

    expected = json.loads((DEFAULT_DIR / "expected.json").read_text())
    latest = expected["published_at"]
    literal = latest.replace("'", "''")
    rows = query(f"SELECT symbol, score_5d, rank_5d, score_21d, rank_21d FROM screener WHERE __time = TIME_PARSE('{literal}')")
    if len(rows) != expected["count"] or not rows:
        raise RuntimeError("The published snapshot is not fully served yet")
    # Duplicate delivery must not silently change portfolio weights.
    by_symbol = {}
    for row in rows:
        if row["symbol"] in by_symbol and row != by_symbol[row["symbol"]]:
            raise RuntimeError("Conflicting rows in the served snapshot")
        by_symbol[row["symbol"]] = row
    model = expected["model"]
    capture(args.output, latest, list(by_symbol.values()), model)

    spark = (SparkSession.builder.appName("track_forward")
             .config("spark.sql.catalog.iceberg", "org.apache.iceberg.spark.SparkCatalog")
             .config("spark.sql.catalog.iceberg.type", "hadoop")
             .config("spark.sql.catalog.iceberg.warehouse", args.warehouse).getOrCreate())
    try:
        # Raw history includes benchmark sessions and recent corrections.
        from pyspark.sql import functions as F
        sys.path.insert(0, str(ROOT / "spark" / "jobs"))
        from daily_history import latest_bars
        daily = latest_bars(spark.table("iceberg.bronze.quotes_daily"))
        snapshots = list((Path(args.output) / "predictions").glob("*.json"))
        earliest = min(json.loads(p.read_text())["publication_date"] for p in snapshots)
        today = datetime.now(IST).date().isoformat()
        bars = daily.filter((F.col("trade_date") > F.lit(earliest).cast("date")) &
                            (F.col("trade_date") < F.lit(today).cast("date"))).select("symbol", "trade_date", "close").collect()
        prices = {(r.symbol, r.trade_date.isoformat()): float(r.close) for r in bars if r.close is not None and r.close > 0}
        sessions = [date for symbol, date in prices if symbol == "^NSEI"]
        report = build_report(args.output, prices, sessions, today)
        print(f"Forward tracking: {len(report['cohorts'])} cohorts")
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
