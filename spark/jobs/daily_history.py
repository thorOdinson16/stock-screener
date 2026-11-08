"""Export committed per-symbol watermarks or deduplicate daily bronze bars.

Watermarks are derived from Iceberg, never from poll attempts or serving state.
The atomic Iceberg overwrite preserves history while replacing overlapping bars.
"""

import argparse
import json
import os

from pyspark.sql import SparkSession, Window
from pyspark.sql import functions as F
from nullability import match_not_null


def latest_bars(frame):
    order = Window.partitionBy("symbol", "trade_date").orderBy(
        F.col("ingested_at").desc(), F.col("close").desc()
    )
    return frame.withColumn("_row", F.row_number().over(order)).filter(
        F.col("_row") == 1
    ).drop("_row")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["watermarks", "deduplicate"])
    parser.add_argument("--warehouse", default="hdfs://localhost:9000/warehouse")
    parser.add_argument("--output")
    parser.add_argument("--reference-output")
    args = parser.parse_args()
    if args.action == "watermarks" and not args.output:
        parser.error("watermarks requires --output")

    spark = (SparkSession.builder.appName("daily_history")
             .config("spark.sql.extensions", "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions")
             .config("spark.sql.catalog.iceberg", "org.apache.iceberg.spark.SparkCatalog")
             .config("spark.sql.catalog.iceberg.type", "hadoop")
             .config("spark.sql.catalog.iceberg.warehouse", args.warehouse)
             .config("spark.sql.session.timeZone", "UTC").getOrCreate())
    try:
        table = "iceberg.bronze.quotes_daily"
        bars = spark.table(table)
        if args.action == "watermarks":
            rows = bars.filter(F.col("close") > 0).groupBy("symbol").agg(
                F.max("trade_date").alias("latest")
            ).collect()
            with open(args.output + ".tmp", "w") as fh:
                json.dump({r.symbol: r.latest.isoformat() for r in rows}, fh)
            os.replace(args.output + ".tmp", args.output)
            if args.reference_output:
                bounds = bars.groupBy("symbol").agg(F.min("trade_date").alias("earliest"), F.max("trade_date").alias("latest"))
                reference = {r.symbol: {"earliest": r.earliest.isoformat(), "closes": {}} for r in bounds.collect()}
                overlap = latest_bars(bars).join(bounds, "symbol").filter(F.col("trade_date") >= F.date_sub(F.col("latest"), 20))
                for row in overlap.select("symbol", "trade_date", "close").collect():
                    if row.close is not None:
                        reference[row.symbol]["closes"][row.trade_date.isoformat()] = float(row.close)
                with open(args.reference_output + ".tmp", "w") as fh:
                    json.dump(reference, fh, allow_nan=False)
                os.replace(args.reference_output + ".tmp", args.reference_output)
        else:
            match_not_null(latest_bars(bars), spark, table).createOrReplaceTempView("daily_history_latest")
            spark.sql(f"INSERT OVERWRITE {table} SELECT * FROM daily_history_latest")
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
