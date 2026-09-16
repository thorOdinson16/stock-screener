"""
metrics.py — pure evaluation metrics for cross-sectional stock ranking.

No Spark/scipy imports: implemented with pandas/numpy so they are unit-testable
(tests/test_features.py) and can run on a collected test-set sample.

A "score" here is any per-symbol prediction (model prediction or rule baseline)
where higher = more attractive. `ret_col` is the realized forward return.
"""

import math

import numpy as np
import pandas as pd

DEFAULT_K = 20


def newey_west_tstat(series, lags: int | None = None) -> dict:
    """Mean/t-stat of a (possibly autocorrelated) series with Newey–West HAC
    standard errors. Overlapping forward-return horizons make the per-date IC /
    portfolio-return series autocorrelated, so a plain t-stat overstates
    significance; `lags` should be at least horizon-1."""
    x = np.asarray([v for v in series if v is not None and not np.isnan(v)], dtype=float)
    n = len(x)
    if n < 3:
        return {"mean": float(x.mean()) if n else np.nan, "t_stat": np.nan,
                "p_value": np.nan, "n": n, "lags": 0}

    mean = float(x.mean())
    d = x - mean
    if lags is None:
        lags = int(math.floor(4 * (n / 100.0) ** (2.0 / 9.0)))
    lags = max(0, min(lags, n - 1))

    gamma0 = float(d @ d) / n
    var = gamma0
    for lag in range(1, lags + 1):
        weight = 1.0 - lag / (lags + 1.0)
        cov = float(d[lag:] @ d[:-lag]) / n
        var += 2.0 * weight * cov

    if var <= 0:
        return {"mean": mean, "t_stat": np.nan, "p_value": np.nan, "n": n, "lags": lags}
    se = math.sqrt(var / n)
    t = mean / se
    # Two-sided p-value using the normal approximation (p = erfc(|t|/sqrt2)).
    p = math.erfc(abs(t) / math.sqrt(2.0))
    return {"mean": mean, "t_stat": float(t), "p_value": float(p), "n": n, "lags": lags}


def deflated_sharpe_ratio(returns, n_trials: int = 1) -> dict:
    """Deflated Sharpe ratio (López de Prado): the probability the observed
    Sharpe is real, after accounting for the number of trials and the
    non-normality (skew/kurtosis) of the return series."""
    x = np.asarray([v for v in returns if v is not None and not np.isnan(v)], dtype=float)
    n = len(x)
    if n < 3 or x.std(ddof=1) == 0:
        return {"sharpe": np.nan, "benchmark_sharpe": np.nan, "dsr": np.nan, "n": n}

    sr = float(x.mean() / x.std(ddof=1))
    skew = float(pd.Series(x).skew())
    kurt = float(pd.Series(x).kurt()) + 3.0  # pandas returns excess kurtosis
    n_trials = max(1, int(n_trials))

    # Expected maximum Sharpe under the null across `n_trials` independent trials.
    euler = 0.5772156649
    if n_trials > 1:
        z1 = _norm_ppf(1.0 - 1.0 / n_trials)
        z2 = _norm_ppf(1.0 - 1.0 / (n_trials * math.e))
        sr0 = (1.0 - euler) * z1 + euler * z2
    else:
        sr0 = 0.0

    denom = math.sqrt(max(1e-12, 1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr**2))
    dsr = _norm_cdf((sr - sr0) * math.sqrt(n - 1) / denom)
    return {"sharpe": sr, "benchmark_sharpe": float(sr0), "dsr": float(dsr), "n": n}


def _norm_cdf(z: float) -> float:
    return 0.5 * math.erfc(-z / math.sqrt(2.0))


def _norm_ppf(p: float) -> float:
    """Acklam's rational approximation to the inverse normal CDF."""
    if p <= 0.0:
        return -math.inf
    if p >= 1.0:
        return math.inf
    a = [-3.969683028665376e01, 2.209460984245205e02, -2.759285104469687e02,
         1.383577518672690e02, -3.066479806614716e01, 2.506628277459239e00]
    b = [-5.447609879822406e01, 1.615858368580409e02, -1.556989798598866e02,
         6.680131188771972e01, -1.328068155288572e01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e00,
         -2.549732539343734e00, 4.374664141464968e00, 2.938163982698783e00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e00,
         3.754408661907416e00]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
               ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
                ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / \
           (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


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
    df: pd.DataFrame,
    score_col: str,
    ret_col: str,
    date_col: str = "trade_date",
    lags: int | None = None,
) -> dict:
    """Per-date rank correlation between score and realized return, averaged,
    with a Newey–West t-stat over the (autocorrelated, overlapping-horizon) IC
    series. Pass `lags` = horizon-1 for daily rebalancing."""
    data = df.dropna(subset=[score_col, ret_col])
    ics = []
    for _, group in data.groupby(date_col):
        ic = _spearman(group[score_col].values, group[ret_col].values)
        if not np.isnan(ic):
            ics.append(ic)

    if not ics:
        return {"ic_mean": np.nan, "ic_std": np.nan, "ic_ir": np.nan,
                "ic_t_stat": np.nan, "ic_p_value": np.nan, "n_dates": 0}

    arr = np.asarray(ics, dtype=float)
    mean = float(arr.mean())
    std = float(arr.std(ddof=1)) if len(arr) > 1 else 0.0
    nw = newey_west_tstat(arr, lags=lags)
    return {
        "ic_mean": mean,
        "ic_std": std,
        "ic_ir": float(mean / std) if std > 0 else np.nan,
        "ic_t_stat": nw["t_stat"],
        "ic_p_value": nw["p_value"],
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
