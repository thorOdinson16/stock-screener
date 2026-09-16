"""
features.py — pure pandas feature/label construction and time-split logic for the
stock-scoring model.

Kept free of Spark imports so it can be unit-tested directly
(tests/test_features.py) and reused inside the Spark jobs via `addPyFile`
(spark/jobs/build_training.py calls `compute_labels` inside applyInPandas).

Label semantics: the raw forward N-trading-day return for a single symbol,
`close[t+N] / close[t] - 1` (undefined / NaN for the last N bars of each
symbol's history), and the training target `excess_ret_Nd` = that raw return
minus the cross-sectional mean on the same date (see `add_excess_returns`).

Feature semantics: the §6.3 technical indicators already computed in
silver.quotes_enriched, then the shared, scale-free derivations and
cross-sectional normalization in `transform.py`. All inputs (rolling means /
ewm / shift) use only data at or before t, so they are point-in-time safe — no
look-ahead. Model feature names live in `transform.MODEL_FEATURES`.
"""

import pandas as pd

from transform import MODEL_FEATURES, RAW_FEATURE_COLUMNS

# Backwards-compatible alias: raw technical indicators as stored in silver.
FEATURE_COLUMNS = list(RAW_FEATURE_COLUMNS)

HORIZONS = (5, 21)

# Raw forward returns (kept for reporting / benchmark aggregation).
RAW_LABEL_COLUMNS = [f"fwd_ret_{h}d" for h in HORIZONS]
# Training targets: cross-sectional excess return over the equal-weight universe.
LABEL_COLUMNS = [f"excess_ret_{h}d" for h in HORIZONS]

# Benchmark index symbols are backfilled through the same daily pipeline
# (poller/backfill.py --include-index) but excluded from the tradable universe and
# from both training and scoring.
BENCHMARK_SYMBOLS = ("^NSEI",)

DEFAULT_TEST_FRACTION = 0.2
DEFAULT_EMBARGO_DAYS = 21


def add_forward_returns(df: pd.DataFrame, horizons=HORIZONS) -> pd.DataFrame:
    """Adds one `fwd_ret_{h}d` column per horizon, per symbol."""
    out = df.sort_values(["symbol", "trade_date"]).reset_index(drop=True)
    for h in horizons:
        future_close = out.groupby("symbol", sort=False)["close"].shift(-h)
        out[f"fwd_ret_{h}d"] = future_close / out["close"] - 1.0
    return out


def add_excess_returns(df: pd.DataFrame) -> pd.DataFrame:
    """Adds `excess_ret_{h}d` = forward return minus the cross-sectional mean
    forward return on the same trade date. Screening is inherently relative, so
    the excess label removes the market/common factor the model cannot trade."""
    out = df.copy()
    for h, raw in zip(HORIZONS, RAW_LABEL_COLUMNS):
        mean = out.groupby("trade_date")[raw].transform("mean")
        out[f"excess_ret_{h}d"] = out[raw] - mean
    return out


def compute_labels(df: pd.DataFrame) -> pd.DataFrame:
    """
    Single-argument entry point for Spark `applyInPandas`.

    Spark inspects the callable's signature: `add_forward_returns` has a second
    (defaulted) parameter, so passing it directly makes Spark fall back to the
    legacy `(key, pdf)` calling convention. This wrapper takes exactly one frame.
    """
    return add_forward_returns(df)


def compute_split_dates(
    sorted_dates,
    test_fraction: float = DEFAULT_TEST_FRACTION,
    embargo_days: int = DEFAULT_EMBARGO_DAYS,
):
    """
    Given the full sorted sequence of distinct trading dates, returns
    `(test_start, embargo_start)`.

    `test_start` is the first date held out for evaluation (the last
    `test_fraction` of the timeline). `embargo_start` is `embargo_days` trading
    dates before it; training rows in `[embargo_start, test_start)` are purged so
    that a training label window (up to `max(HORIZONS)` days forward) cannot
    straddle the train/test boundary and leak information.
    """
    n = len(sorted_dates)
    if n < 3:
        raise ValueError(f"need at least 3 distinct dates to split (got {n})")

    test_start_idx = int(round(n * (1.0 - test_fraction)))
    test_start_idx = min(max(test_start_idx, 1), n - 1)
    embargo_start_idx = max(0, test_start_idx - embargo_days)
    return sorted_dates[test_start_idx], sorted_dates[embargo_start_idx]


def assign_split(
    df: pd.DataFrame,
    sorted_dates,
    test_fraction: float = DEFAULT_TEST_FRACTION,
    embargo_days: int = DEFAULT_EMBARGO_DAYS,
) -> pd.DataFrame:
    """Adds a `split` column with values train / embargo / test."""
    test_start, embargo_start = compute_split_dates(sorted_dates, test_fraction, embargo_days)
    out = df.copy()
    out["split"] = "train"
    out.loc[out["trade_date"] >= embargo_start, "split"] = "embargo"
    out.loc[out["trade_date"] >= test_start, "split"] = "test"
    return out
