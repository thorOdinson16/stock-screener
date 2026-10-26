"""
spark_schema.py — Spark-side glue for the shared feature transform: the
StructType of its output, and the point-in-time fundamentals join.

Kept separate from transform.py so the latter stays importable without pyspark
(the unit tests run without a Spark install). Only the Spark jobs import this.
"""

import pandas as pd
from pyspark.sql import functions as F
from pyspark.sql.utils import AnalysisException
from pyspark.sql.types import DateType, DoubleType, StringType, StructField, StructType

from extra_features import STATEMENT_COLUMNS, FUNDAMENTAL_FEATURES, add_fundamental_features
from transform import MODEL_FEATURES

# Jobs that apply the transform ship these modules to the executors.
FEATURE_MODULES = ("features.py", "transform.py", "extra_features.py", "asof.py")


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


def attach_fundamentals(spark, base):
    """Adds the raw FUNDAMENTAL_FEATURES to `base` (needs symbol, trade_date,
    close): each row gets the latest annual statement published by its trade
    date (extra_features.add_fundamental_features). Shared by build_training.py
    and score_stocks.py so training and scoring see identical values. Without
    statements the columns are null and the transform treats them as neutral."""
    try:
        table = spark.table("iceberg.silver.financial_statements")
    except AnalysisException:  # table not created on an older install
        table = None
    # Only the columns the features need, with the date as a string: Spark 3.3's
    # toPandas() cannot convert timestamp columns under pandas 2.
    statements = (
        pd.DataFrame() if table is None else table.select(
            "symbol", F.col("period_end").cast("string").alias("period_end"),
            *STATEMENT_COLUMNS,
        ).toPandas()
    )
    if statements.empty:
        print("No silver.financial_statements — fundamental features are neutral.")
        return base.select(
            "*", *[F.lit(None).cast("double").alias(c) for c in FUNDAMENTAL_FEATURES]
        )

    columns = base.columns
    schema = StructType(
        base.schema.fields + [StructField(c, DoubleType()) for c in FUNDAMENTAL_FEATURES]
    )

    def join(pdf: pd.DataFrame) -> pd.DataFrame:
        out = add_fundamental_features(pdf, statements)
        out["trade_date"] = pd.to_datetime(out["trade_date"]).dt.date
        return out[columns + FUNDAMENTAL_FEATURES]

    return base.groupBy("symbol").applyInPandas(join, schema=schema)
