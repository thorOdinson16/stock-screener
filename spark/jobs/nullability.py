"""
nullability.py — make DataFrame columns non-nullable to match NOT NULL Iceberg columns.

Spark 3.3 rejects `INSERT ... SELECT` when a nullable column feeds a NOT NULL
table column ("Cannot write nullable values to non-null column"). Spark 4.x
accepted this. Columns that come out of applyInPandas / pandas UDFs are always
marked nullable, so wrap them in a COALESCE with a typed literal, which Spark
infers as non-nullable. The literal is never used for real rows: the table
columns in question hold keys/timestamps that are always populated.
"""

import datetime

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql import types as T


def _default(dtype: T.DataType):
    if isinstance(dtype, T.StringType):
        return F.lit("")
    if isinstance(dtype, T.DateType):
        return F.lit(datetime.date(1970, 1, 1))
    if isinstance(dtype, T.TimestampType):
        return F.lit(datetime.datetime(1970, 1, 1))
    if isinstance(dtype, T.BooleanType):
        return F.lit(False)
    return F.lit(0).cast(dtype)  # numeric casts of a literal 0 are never null


def match_not_null(df: DataFrame, spark: SparkSession, table: str) -> DataFrame:
    """Return `df` with columns that are NOT NULL in `table` marked non-nullable."""
    target = {f.name: f for f in spark.table(table).schema.fields}
    cols = []
    for field in df.schema.fields:
        t = target.get(field.name)
        if t is not None and not t.nullable and field.nullable:
            cols.append(F.coalesce(F.col(field.name), _default(field.dataType)).alias(field.name))
        else:
            cols.append(F.col(field.name))
    return df.select(*cols)
