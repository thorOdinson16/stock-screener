"""
metrics.py — pure evaluation metrics for cross-sectional stock ranking.

No Spark/scipy imports: implemented with pandas/numpy so they are unit-testable
(tests/test_features.py) and can run on a collected test-set sample.

A "score" here is any per-symbol prediction (model prediction or rule baseline)
where higher = more attractive. `ret_col` is the realized forward return.
"""

import numpy as np
import pandas as pd

DEFAULT_K = 20


def _spearman(a, b) -> float:
    """Spearman rank correlation; NaN for <2 points or a constant series."""
    if len(a) < 2:
        return np.nan
    ra = pd.Series(a).rank()
    rb = pd.Series(b).rank()
    if ra.std() == 0 or rb.std() == 0:
        return np.nan
    return float(ra.corr(rb))


def information_coefficient(
    df: pd.DataFrame, score_col: str, ret_col: str, date_col: str = "trade_date"
) -> dict:
    """Per-date rank correlation between score and realized return, averaged."""
    data = df.dropna(subset=[score_col, ret_col])
    ics = []
    for _, group in data.groupby(date_col):
        ic = _spearman(group[score_col].values, group[ret_col].values)
        if not np.isnan(ic):
            ics.append(ic)

    if not ics:
        return {"ic_mean": np.nan, "ic_std": np.nan, "ic_ir": np.nan, "n_dates": 0}

    arr = np.asarray(ics, dtype=float)
    mean = float(arr.mean())
    std = float(arr.std(ddof=1)) if len(arr) > 1 else 0.0
    return {
        "ic_mean": mean,
        "ic_std": std,
        "ic_ir": float(mean / std) if std > 0 else np.nan,
        "n_dates": int(len(arr)),
    }


def precision_at_k(
    df: pd.DataFrame,
    score_col: str,
    ret_col: str,
    k: int = DEFAULT_K,
    benchmark_col: str | None = None,
    date_col: str = "trade_date",
) -> dict:
    """
    Of the top-k scored symbols each date, the fraction that beat the benchmark
    (if `benchmark_col` is given) or simply had a positive forward return.
    """
    subset = [score_col, ret_col] + ([benchmark_col] if benchmark_col else [])
    data = df.dropna(subset=subset)

    hits = []
    for _, group in data.groupby(date_col):
        top = group.nlargest(k, score_col)
        if top.empty:
            continue
        target = top[benchmark_col] if benchmark_col else 0.0
        hits.append(float((top[ret_col] > target).mean()))

    return {
        "precision_at_k": float(np.mean(hits)) if hits else np.nan,
        "k": k,
        "benchmark": benchmark_col or "0.0",
        "n_dates": len(hits),
    }


def regression_metrics(df: pd.DataFrame, pred_col: str, actual_col: str) -> dict:
    data = df.dropna(subset=[pred_col, actual_col])
    if data.empty:
        return {"rmse": np.nan, "mae": np.nan, "n": 0}
    err = data[pred_col] - data[actual_col]
    return {
        "rmse": float(np.sqrt((err**2).mean())),
        "mae": float(err.abs().mean()),
        "n": int(len(data)),
    }


def top_k_forward_returns(
    df: pd.DataFrame,
    score_col: str,
    ret_col: str,
    k: int = DEFAULT_K,
    date_col: str = "trade_date",
) -> pd.Series:
    """
    Equal-weight mean forward return of the top-k scored symbols, per date.

    Note: forward returns for adjacent dates overlap for horizon > 1, so the
    series is a sequence of per-date average forward returns (a signal-quality
    measure), not a compounding equity curve.
    """
    data = df.dropna(subset=[score_col, ret_col])
    rows = {}
    for date, group in data.groupby(date_col):
        top = group.nlargest(k, score_col)
        if not top.empty:
            rows[date] = float(top[ret_col].mean())
    return pd.Series(rows).sort_index()


def summarize_forward_returns(series: pd.Series) -> dict:
    if series.empty:
        return {"mean": np.nan, "periods": 0}
    return {"mean": float(series.mean()), "periods": int(len(series))}


def benchmark_forward_return(
    df: pd.DataFrame, benchmark_col: str, date_col: str = "trade_date"
) -> float:
    data = df.dropna(subset=[benchmark_col])
    if data.empty:
        return np.nan
    per_date = data.groupby(date_col)[benchmark_col].first()
    return float(per_date.mean())


def turnover(
    df: pd.DataFrame,
    score_col: str,
    symbol_col: str = "symbol",
    k: int = DEFAULT_K,
    date_col: str = "trade_date",
) -> float:
    """Average fraction of the top-k set that changes between consecutive dates."""
    data = df.dropna(subset=[score_col])
    rates = []
    previous = None
    for _, group in data.groupby(date_col):
        current = set(group.nlargest(k, score_col)[symbol_col])
        if previous is not None and current:
            union = current | previous
            rates.append(1.0 - len(current & previous) / len(union) if union else 0.0)
        previous = current
    return float(np.mean(rates)) if rates else np.nan
