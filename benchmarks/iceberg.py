"""
Experiment 5 — Iceberg schema evolution, time travel, compaction, partition
evolution (roadmap §4). Run with spark-submit:

    spark-submit \
      --packages org.apache.iceberg:iceberg-spark-runtime-3.3_2.12:1.8.1 \
      benchmarks/iceberg.py

Uses scratch tables under the `bench` namespace so production tables are never
mutated. Writes benchmarks/iceberg/{iceberg.json,summary.md}.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

from pyspark.sql import SparkSession

_HERE = os.path.dirname(os.path.abspath(__file__))
_OUT = os.path.join(_HERE, "iceberg")


def build_spark(warehouse: str) -> SparkSession:
    return (
        SparkSession.builder.appName("iceberg_experiment")
        .config(
            "spark.sql.extensions",
            "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions",
        )
        .config("spark.sql.catalog.iceberg", "org.apache.iceberg.spark.SparkCatalog")
        .config("spark.sql.catalog.iceberg.type", "hadoop")
        .config("spark.sql.catalog.iceberg.warehouse", warehouse)
        .config("spark.sql.defaultCatalog", "iceberg")
        .config("spark.sql.session.timeZone", "UTC")
        .getOrCreate()
    )


def _files(spark, table):
    return spark.sql(f"SELECT COUNT(*) AS n FROM {table}.files").first()["n"]


def schema_evolution(spark) -> dict:
    spark.sql("CREATE NAMESPACE IF NOT EXISTS iceberg.bench")
    spark.sql(
        "CREATE TABLE IF NOT EXISTS iceberg.bench.evolution USING iceberg "
        "AS SELECT * FROM iceberg.gold.top_picks"
    )
    cols_before = list(spark.table("iceberg.bench.evolution").columns)
    rows_before = spark.table("iceberg.bench.evolution").count()

    spark.sql("ALTER TABLE iceberg.bench.evolution ADD COLUMN bench_note STRING")
    cols_after = list(spark.table("iceberg.bench.evolution").columns)
    return {
        "columns_before": cols_before,
        "columns_after": cols_after,
        "added_column": "bench_note",
        "rows_before": rows_before,
        "old_data_still_readable": spark.table("iceberg.bench.evolution").count() == rows_before,
    }


def time_travel(spark) -> dict:
    snapshots = [
        row.asDict()
        for row in spark.sql(
            "SELECT snapshot_id, committed_at FROM iceberg.bench.evolution.snapshots "
            "ORDER BY committed_at"
        ).collect()
    ]
    result = {"n_snapshots": len(snapshots), "version_as_of": None, "timestamp_as_of": None}
    if snapshots:
        first = snapshots[0]
        sid = first["snapshot_id"]
        committed = first["committed_at"]
        result["version_as_of"] = spark.sql(
            f"SELECT COUNT(*) AS n FROM iceberg.bench.evolution VERSION AS OF {sid}"
        ).first()["n"]
        result["timestamp_as_of"] = spark.sql(
            f"SELECT COUNT(*) AS n FROM iceberg.bench.evolution "
            f"TIMESTAMP AS OF '{committed.isoformat()}'"
        ).first()["n"]
    return result


def compaction(spark) -> dict:
    before = _files(spark, "iceberg.bench.evolution")
    spark.sql(
        "CALL iceberg.system.rewrite_data_files(table => 'iceberg.bench.evolution')"
    )
    spark.sql(
        "CALL iceberg.system.rewrite_manifests(table => 'iceberg.bench.evolution')"
    )
    return {"files_before": before, "files_after": _files(spark, "iceberg.bench.evolution")}


def partition_evolution(spark) -> dict:
    spark.sql("DROP TABLE IF EXISTS iceberg.bench.partitioned")
    spark.sql(
        "CREATE TABLE iceberg.bench.partitioned "
        "(symbol STRING, trade_date DATE, score DOUBLE) USING iceberg "
        "PARTITIONED BY (months(trade_date))"
    )
    spark.sql(
        "INSERT INTO iceberg.bench.partitioned VALUES "
        "('A', DATE '2026-01-02', 1.0), ('B', DATE '2026-02-03', 2.0)"
    )
    before = [r["partition"] for r in spark.sql(
        "SELECT partition FROM iceberg.bench.partitioned.partitions ORDER BY partition"
    ).collect()]
    spark.sql("ALTER TABLE iceberg.bench.partitioned ADD PARTITION FIELD days(trade_date)")
    spark.sql(
        "INSERT INTO iceberg.bench.partitioned VALUES ('C', DATE '2026-02-04', 3.0)"
    )
    after = [r["partition"] for r in spark.sql(
        "SELECT partition FROM iceberg.bench.partitioned.partitions ORDER BY partition"
    ).collect()]
    return {"partitions_before": [str(p) for p in before],
            "partitions_after": [str(p) for p in after],
            "row_count": spark.table("iceberg.bench.partitioned").count()}


def main():
    parser = argparse.ArgumentParser(description="Iceberg experiment")
    parser.add_argument("--warehouse", default="hdfs://localhost:9000/warehouse")
    args = parser.parse_args()

    spark = build_spark(args.warehouse)
    spark.sparkContext.setLogLevel("WARN")

    payload = {"recorded_at": datetime.now(timezone.utc).isoformat()}
    for name, fn in (
        ("schema_evolution", schema_evolution),
        ("time_travel", time_travel),
        ("compaction", compaction),
        ("partition_evolution", partition_evolution),
    ):
        try:
            payload[name] = fn(spark)
        except Exception as e:  # noqa: BLE001 — one demo must not abort the rest
            payload[name] = {"error": str(e)}
        print(f"{name}: {payload[name]}")

    os.makedirs(_OUT, exist_ok=True)
    with open(os.path.join(_OUT, "iceberg.json"), "w") as fh:
        json.dump(payload, fh, indent=2, default=str)

    lines = ["# Iceberg", ""]
    se = payload["schema_evolution"]
    lines += ["## Schema evolution",
              f"- columns before: {se.get('columns_before')}",
              f"- columns after: {se.get('columns_after')}",
              f"- old data readable: {se.get('old_data_still_readable')}", ""]
    tt = payload["time_travel"]
    lines += ["## Time travel",
              f"- snapshots: {tt.get('n_snapshots')}",
              f"- rows VERSION AS OF first: {tt.get('version_as_of')}",
              f"- rows TIMESTAMP AS OF first: {tt.get('timestamp_as_of')}", ""]
    cp = payload["compaction"]
    lines += ["## Compaction",
              f"- files {cp.get('files_before')} -> {cp.get('files_after')}", ""]
    pe = payload["partition_evolution"]
    lines += ["## Partition evolution",
              f"- partitions before: {pe.get('partitions_before')}",
              f"- partitions after: {pe.get('partitions_after')}", ""]
    with open(os.path.join(_OUT, "summary.md"), "w") as fh:
        fh.write("\n".join(lines) + "\n")
    print(f"wrote {_OUT}")

    spark.stop()


if __name__ == "__main__":
    main()
