"""
create_tables.py — create the Iceberg namespaces and tables from iceberg/schemas.

Docker counterpart of setup.md §1.3. The native runbook uses the `spark-sql` CLI,
which always opens a Hive client (and so needs the Hive Metastore, which the
Docker stack doesn't run); this runs the same DDL files through spark.sql()
with the in-memory catalog the pipeline jobs use.
"""

import argparse
import os
import re

from pyspark.sql import SparkSession

SCHEMAS = ["bronze-schema.sql", "silver-schema.sql", "gold-schema.sql"]


def statements(path: str):
    with open(path) as fh:
        text = re.sub(r"--[^\n]*", "", fh.read())
    return [s.strip() for s in text.split(";") if s.strip()]


def main():
    parser = argparse.ArgumentParser(description="Create Iceberg tables")
    parser.add_argument("--warehouse", default="hdfs://localhost:9000/warehouse")
    args = parser.parse_args()

    spark = (
        SparkSession.builder.appName("create_tables")
        .config(
            "spark.sql.extensions",
            "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions",
        )
        .config("spark.sql.catalog.iceberg", "org.apache.iceberg.spark.SparkCatalog")
        .config("spark.sql.catalog.iceberg.type", "hadoop")
        .config("spark.sql.catalog.iceberg.warehouse", args.warehouse)
        .config("spark.sql.defaultCatalog", "iceberg")
        .config("spark.sql.session.timeZone", "UTC")
        .getOrCreate()
    )

    schema_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "iceberg", "schemas")
    spark.sql("CREATE NAMESPACE IF NOT EXISTS bronze")
    for name in SCHEMAS:
        for stmt in statements(os.path.join(schema_dir, name)):
            spark.sql(stmt)
        print(f"applied {name}")

    for ns in ["bronze", "silver", "gold", "ml"]:
        tables = [r.tableName for r in spark.sql(f"SHOW TABLES IN {ns}").collect()]
        print(f"{ns}: {', '.join(tables) or '(none)'}")
    spark.stop()


if __name__ == "__main__":
    main()
