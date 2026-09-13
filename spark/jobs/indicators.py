"""
indicators.py — pure pandas technical-indicator computation for one symbol's
daily bars. Kept free of any Spark imports so it can be unit-tested directly
(see tests/test_indicators.py) and reused inside the Spark job via
`groupBy("symbol").applyInPandas(...)`.

Input frame columns: symbol, trade_date, close, volume.
Output frame columns: symbol, trade_date, close, volume + the feature columns.

Conventions:
  volatility_20d            std-dev of daily returns (fraction)
  volume_ratio              volume / 20-day average volume
  distance_from_52w_high    (close - 252d high) / 252d high   (fraction, <= 0)
  distance_from_52w_low     (close - 252d low)  / 252d low    (fraction, >= 0)
  price_momentum_1m/3m/6m   close / close.shift(21/63/126) - 1 (fraction)
NA is emitted during the warm-up window (e.g. sma_200 is null until 200 bars
are available).
"""

import numpy as np
import pandas as pd

# Trading-day lookbacks.
SMA_WINDOWS = (20, 50, 200)
MOMENTUM_LOOKBACKS = {"price_momentum_1m": 21, "price_momentum_3m": 63, "price_momentum_6m": 126}
YEAR_WINDOW = 252  # ~52 trading weeks
RSI_PERIOD = 14

FEATURE_COLUMNS = [
    "symbol",
    "trade_date",
    "close",
    "volume",
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


def _rsi(close: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    """Wilder's RSI using exponential smoothing (alpha = 1/period)."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()

    rs = avg_gain / avg_loss
    rsi = 100.0 - (100.0 / (1.0 + rs))
    # All-gain windows have avg_loss == 0 => RSI 100; flat windows stay NaN.
    rsi = rsi.mask((avg_loss == 0) & (avg_gain > 0), 100.0)
    return rsi


def compute_features(group: pd.DataFrame) -> pd.DataFrame:
    """Compute all features for a single symbol's daily bars (unsorted input OK)."""
    df = group.sort_values("trade_date").reset_index(drop=True)
    close = df["close"].astype(float)
    volume = df["volume"].astype(float)
    returns = close.pct_change()

    out = df[["symbol", "trade_date", "close", "volume"]].copy()

    for window in SMA_WINDOWS:
        out[f"sma_{window}"] = close.rolling(window, min_periods=window).mean()

    ema_12 = close.ewm(span=12, adjust=False).mean()
    ema_26 = close.ewm(span=26, adjust=False).mean()
    out["ema_12"] = ema_12
    out["ema_26"] = ema_26

    macd = ema_12 - ema_26
    out["macd"] = macd
    out["macd_signal"] = macd.ewm(span=9, adjust=False).mean()

    out["rsi_14"] = _rsi(close)

    out["volatility_20d"] = returns.rolling(20, min_periods=20).std()

    volume_avg_20d = volume.rolling(20, min_periods=20).mean()
    out["volume_avg_20d"] = volume_avg_20d
    out["volume_ratio"] = volume / volume_avg_20d

    high_252 = close.rolling(YEAR_WINDOW, min_periods=1).max()
    low_252 = close.rolling(YEAR_WINDOW, min_periods=1).min()
    out["distance_from_52w_high"] = (close - high_252) / high_252
    out["distance_from_52w_low"] = (close - low_252) / low_252

    for name, lookback in MOMENTUM_LOOKBACKS.items():
        out[name] = close / close.shift(lookback) - 1.0

    return out.replace([np.inf, -np.inf], np.nan)[FEATURE_COLUMNS]
