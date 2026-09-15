"""
maintain_iceberg.py — Iceberg table maintenance: compaction, manifest rewrite and
snapshot expiry.

Per table:
    rewrite_data_files   (compact small files)        — Spark procedure
    rewrite_manifests    (consolidate manifests)      — Spark procedure
    expire_snapshots     (older_than N days)          — driver-side Iceberg API

Snapshot expiry uses the base `Table.expireSnapshots()` API rather than the Spark
procedure: the procedure distributes deletes through a Spark job that hangs in
this Spark 4.1.3 + Iceberg 1.11 runtime. (Orphan-file cleanup is not available in
this bundled runtime — `org.apache.iceberg.actions.RemoveOrphanFiles` is not
included — so it is intentionally omitted.)

Run (from the repo root):
    spark-submit \
      --packages org.apache.iceberg:iceberg-spark-runtime-4.1_2.13:1.11.0 \
      spark/jobs/maintain_iceberg.py --snapshot-retention-days 7
"""

import argparse
from datetime import datetime, timedelta, timezone

from pyspark.sql import SparkSession

DEFAULT_TABLES = [
    "iceberg.bronze.market_quotes",
    "iceberg.bronze.quotes_daily",
    "iceberg.bronze.market_fundamentals",
    "iceberg.silver.quotes_enriched",
    "iceberg.silver.fundamentals_clean",
    "iceberg.gold.stock_scores",
    "iceberg.gold.top_picks",
    "iceberg.ml.training_dataset",
]

CATALOG_PREFIX = "iceberg."


def meta_name(table: str) -> str:
    """Metadata tables (`.snapshots`/`.files`) resolve without the catalog prefix."""
    return table[len(CATALOG_PREFIX):] if table.startswith(CATALOG_PREFIX) else table


def build_spark(warehouse: str) -> SparkSession:
    return (
        SparkSession.builder.appName("maintain_iceberg")
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


def table_stats(spark: SparkSession, table: str) -> dict:
    name = meta_name(table)
    snapshots = spark.sql(f"SELECT COUNT(*) AS n FROM {name}.snapshots").first()["n"]
    row = spark.sql(
        f"SELECT COUNT(*) AS files, COALESCE(SUM(file_size_in_bytes), 0) AS bytes FROM {name}.files"
    ).first()
    return {"snapshots": snapshots, "files": row["files"], "mb": row["bytes"] / 1e6}


def one_line(stats: dict) -> str:
    return f"{stats['snapshots']} snapshots, {stats['files']} files, {stats['mb']:.1f} MB"


def maintain(spark: SparkSession, table: str, snapshot_ts_ms: int) -> None:
    try:
        before = table_stats(spark, table)
    except Exception as e:  # noqa: BLE001 — a missing table must not abort the rest
        print(f"SKIP {table}: {e}")
        return

    print(f"{table}: before = {one_line(before)}")

    rows = spark.sql(f"CALL iceberg.system.rewrite_data_files(table => '{table}')").collect()
    for row in rows:
        print(
            f"  rewrite_data_files: rewrote {row['rewritten_data_files_count']} files, "
            f"added {row['added_data_files_count']}"
        )

    spark.sql(f"CALL iceberg.system.rewrite_manifests(table => '{table}')")

    jtable = spark._jvm.org.apache.iceberg.spark.Spark3Util.loadIcebergTable(
        spark._jsparkSession, table
    )
    jtable.expireSnapshots().expireOlderThan(snapshot_ts_ms).commit()

    after = table_stats(spark, table)
    expired = before["snapshots"] - after["snapshots"]
    print(f"{table}: after  = {one_line(after)} (expired {expired} snapshots)")


def main():
    parser = argparse.ArgumentParser(description="Iceberg maintenance")
    parser.add_argument("--warehouse", default="hdfs://localhost:9000/warehouse")
    parser.add_argument("--snapshot-retention-days", type=int, default=7)
    parser.add_argument("--tables", default=",".join(DEFAULT_TABLES))
    parser.add_argument("--dry-run", action="store_true", help="Print current stats only")
    args = parser.parse_args()

    spark = build_spark(args.warehouse)
    spark.sparkContext.setLogLevel("WARN")

    tables = [t.strip() for t in args.tables.split(",") if t.strip()]
    now = datetime.now(timezone.utc)
    snapshot_cutoff = now - timedelta(days=args.snapshot_retention_days)
    snapshot_ts_ms = int(snapshot_cutoff.timestamp() * 1000)

    if args.dry_run:
        print(f"DRY RUN (expire snapshots < {snapshot_cutoff.isoformat()})")
        for table in tables:
            try:
                print(f"{table}: {one_line(table_stats(spark, table))}")
            except Exception as e:  # noqa: BLE001
                print(f"SKIP {table}: {e}")
        spark.stop()
        return

    print(f"Maintenance: expire snapshots < {snapshot_cutoff.isoformat()}")
    for table in tables:
        maintain(spark, table, snapshot_ts_ms)

    print("Maintenance complete.")
    spark.stop()


if __name__ == "__main__":
    main()
