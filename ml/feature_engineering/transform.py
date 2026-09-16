"""
transform.py — shared, pure-pandas feature transforms for the stock-scoring model.

This is the single source of truth for how model features are derived and
normalized. It is applied identically at training time
(spark/jobs/build_training.py) and at scoring time (spark/jobs/score_stocks.py),
so train/serve parity is structural rather than a convention: both jobs call
`transform_group` inside `groupBy("trade_date").applyInPandas(...)`.

Two stages:

  1. `add_derived_features` replaces absolute price-level features (sma_*/ema_*
     and macd, which are in rupees) with scale-free ratio forms, so a pooled
     cross-symbol model cannot learn price scale.
  2. `apply_cross_sectional` normalizes each feature within its trade date
     (winsorized z-score by default) so features are comparable across the
     cross-section on any given day. Optionally sector-demeans first.

Kept free of Spark imports so it can be unit-tested directly
(tests/test_features.py) and shipped to executors via `addPyFile`.
"""

import numpy as np
import pandas as pd

# Raw technical indicators as stored in silver.quotes_enriched.
RAW_FEATURE_COLUMNS = [
    "sma_20",
    "sma_50",
    "sma_200",
    "ema_12",
    "ema_26",
    "rsi_14",
    "macd",
    "macd_signal",
    "volatility_20d",
    "volume_avg_20d",
    "volume_ratio",
    "distance_from_52w_high",
    "distance_from_52w_low",
    "price_momentum_1m",
    "price_momentum_3m",
    "price_momentum_6m",
]

# Ratio recipes: derived = raw numerator / raw denominator - 1.
_RATIO_RECIPES = {
    "close_over_sma_200": ("close", "sma_200"),
    "sma_50_over_sma_200": ("sma_50", "sma_200"),
    "sma_20_over_sma_50": ("sma_20", "sma_50"),
    "ema_12_over_ema_26": ("ema_12", "ema_26"),
}

# MACD is an absolute price difference; scale it by a moving average instead of
# turning it into a ratio of two same-unit series.
_MACD_RECIPES = {
    "macd_over_sma_50": ("macd", "sma_50"),
    "macd_signal_over_sma_50": ("macd_signal", "sma_50"),
}

# Indicators that are already scale-free / cross-sectionally comparable.
_PASSTHROUGH_FEATURES = [
    "rsi_14",
    "volatility_20d",
    "volume_ratio",
    "distance_from_52w_high",
    "distance_from_52w_low",
    "price_momentum_1m",
    "price_momentum_3m",
    "price_momentum_6m",
]

# Derived, scale-free model inputs (order is stable — it is written into schemas).
MODEL_FEATURES = (
    list(_RATIO_RECIPES)
    + list(_MACD_RECIPES)
    + _PASSTHROUGH_FEATURES
)

# Cross-sectional normalization defaults.
NORMALIZATION_METHOD = "zscore"  # "zscore" | "rank"
WINSOR_LIMITS = (0.01, 0.99)
SECTOR_NEUTRAL = False


def add_derived_features(df: pd.DataFrame) -> pd.DataFrame:
    """Adds the scale-free `MODEL_FEATURES` columns to a frame that contains the
    raw indicators plus `close`. Row-wise, so no ordering requirement."""
    out = df.copy()
    for name, (num, den) in {**_RATIO_RECIPES, **_MACD_RECIPES}.items():
        offset = -1.0 if name in _RATIO_RECIPES else 0.0
        out[name] = out[num] / out[den] + offset
    return out.replace([np.inf, -np.inf], np.nan)


def _winsorize(series: pd.Series, lower: float, upper: float) -> pd.Series:
    if series.notna().sum() < 2:
        return series
    lo, hi = series.quantile(lower), series.quantile(upper)
    if pd.isna(lo) or pd.isna(hi) or hi < lo:
        return series
    return series.clip(lo, hi)


# Below this, the cross-section is constant and (x - mean) / std is pure
# floating-point noise (e.g. a single regime), so emit neutral zeros.
_STD_EPS = 1e-12


def _normalize_group(series: pd.Series, method: str, winsor: tuple) -> pd.Series:
    series = _winsorize(series, *winsor)
    if method == "rank":
        return series.rank(pct=True) - 0.5
    std = float(series.std(ddof=0))
    if not np.isfinite(std) or std < _STD_EPS:
        return series * 0.0
    return (series - series.mean()) / std


def apply_cross_sectional(
    df: pd.DataFrame,
    columns=None,
    date_col: str = "trade_date",
    method: str = NORMALIZATION_METHOD,
    winsor: tuple = WINSOR_LIMITS,
    sector_col: str | None = None,
    sector_neutral: bool = SECTOR_NEUTRAL,
) -> pd.DataFrame:
    """Normalizes `columns` within each `date_col` slice (winsorized z-score or
    rank). With `sector_neutral`, subtracts each (date, sector) mean first, then
    normalizes across the date."""
    columns = list(columns or MODEL_FEATURES)
    out = df.copy()

    if sector_neutral and sector_col and sector_col in out.columns:
        out[columns] = out[columns] - out.groupby(
            [date_col, sector_col], sort=False
        )[columns].transform("mean")

    for col in columns:
        out[col] = out.groupby(date_col, sort=False)[col].transform(
            _normalize_group, method=method, winsor=winsor
        )
    return out


def transform_group(pdf: pd.DataFrame) -> pd.DataFrame:
    """Single-argument Spark `applyInPandas` entry point (one trade date per
    group): derive then cross-sectionally normalize the model features."""
    out = add_derived_features(pdf)
    out = apply_cross_sectional(out, columns=MODEL_FEATURES)
    return out[["symbol", "trade_date"] + MODEL_FEATURES]
