"""
spark_schema.py — Spark StructType for the shared feature transform output.

Kept separate from transform.py so the latter stays importable without pyspark
(the unit tests run without a Spark install). Only the Spark jobs import this.
"""

from pyspark.sql.types import DateType, DoubleType, StringType, StructField, StructType

from transform import MODEL_FEATURES


def normalized_schema() -> StructType:
    """Output of `transform.transform_group` inside applyInPandas."""
    return StructType(
        [
            StructField("symbol", StringType()),
            StructField("trade_date", DateType()),
        ]
        + [StructField(c, DoubleType()) for c in MODEL_FEATURES]
    )


NORMALIZED_SCHEMA = normalized_schema()
