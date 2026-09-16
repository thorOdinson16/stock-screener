"""
asof.py — point-in-time ("as-of") fundamentals join.

Fundamentals in `bronze.market_fundamentals` are append-only and timestamped, so
a training row for `(symbol, trade_date)` may only use the most recent
fundamental snapshot that was knowable by the end of that trading day. Naively
joining the *latest* fundamentals would leak the future.

`asof_join_fundamentals` is implemented now (pure pandas, unit-tested) and is
enabled in `build_training.py` once enough fundamentals history has accrued;
until then the model is technical-only by construction.
"""

import pandas as pd

# Fundamental fields useful as model features (52-week high/low are price-derived
# and already represented by the technical features, so they are excluded).
FUNDAMENTAL_FEATURE_COLUMNS = [
    "pe_ratio",
    "pb_ratio",
    "eps",
    "dividend_yield",
    "market_cap",
    "beta",
    "return_on_equity",
    "debt_to_equity",
    "revenue_growth",
    "earnings_growth",
]


def asof_join_fundamentals(
    panel: pd.DataFrame,
    fundamentals: pd.DataFrame,
    on: str = "trade_date",
    fund_time: str = "timestamp",
    by: str = "symbol",
    columns=None,
    offset: pd.Timedelta = pd.Timedelta(days=1),
) -> pd.DataFrame:
    """Left-joins the latest fundamental snapshot per `by` group with
    `fund_time <= (on + offset)`. The default offset (end of the trade day)
    includes snapshots published during the day and never looks ahead.

    `panel[on]` may be date-like; `fundamentals[fund_time]` must be datetime-like.
    Returned rows are sorted by `[by, on]`; fundamental columns are suffixed
    `_fund` only on collision with existing panel columns.
    """
    columns = list(columns or FUNDAMENTAL_FEATURE_COLUMNS)
    right_cols = [by, fund_time] + [c for c in columns if c not in (by, fund_time)]

    left = panel.copy()
    left[on] = pd.to_datetime(left[on])
    left = left.sort_values([by, on]).reset_index(drop=True)

    right = fundamentals[[c for c in right_cols if c in fundamentals.columns]].copy()
    right[fund_time] = pd.to_datetime(right[fund_time])
    right = right.sort_values([by, fund_time]).reset_index(drop=True)

    # Shift the decision time so the same trading day's snapshot is eligible.
    decision = left[on] + offset
    merged = pd.merge_asof(
        left,
        right,
        left_on=decision,
        right_on=fund_time,
        by=by,
        direction="backward",
        allow_exact_matches=True,
        suffixes=("", "_fund"),
    )
    return merged.reset_index(drop=True)


def latest_asof(panel: pd.DataFrame, fundamentals: pd.DataFrame, timestamp, **kwargs):
    """Convenience for scoring a single cross-section as of one timestamp."""
    snapshot = panel.copy()
    snapshot["trade_date"] = pd.to_datetime(timestamp)
    return asof_join_fundamentals(snapshot, fundamentals, **kwargs)
