"""
extra_features.py — additional point-in-time model inputs beyond the silver
technical indicators: per-symbol time-series signals (short-term reversal, 12-1
momentum, volume trend, beta / idiosyncratic volatility against the index, max
daily return, illiquidity) and fundamentals from annual financial statements.

Pure pandas (no Spark) so it is unit-testable and can be shipped to executors
via `addPyFile`, like transform.py. Every input uses only data available at or
before the row's trade date:

  * time-series features use trailing windows / backward shifts only;
  * statements are joined as-of with a publication lag (asof.py), so a fiscal
    year's numbers are only used `STATEMENT_LAG_DAYS` after the year ends.

Outputs are raw values; cross-sectional normalization happens in transform.py.
"""

import numpy as np
import pandas as pd

from asof import asof_join_fundamentals

# Per-symbol time-series features (need the symbol's daily history + the index).
TIMESERIES_FEATURES = [
    "reversal_5d",       # last week's return (short-term reversal)
    "momentum_12_1",     # 12-month return excluding the last month
    "volume_trend",      # 5d / 60d average volume
    "beta_120d",         # beta of daily returns to the index over ~6 months
    "idio_vol_120d",     # residual volatility of that regression
    "max_return_21d",    # largest daily return in the last month
    "illiquidity_21d",   # Amihud: mean |return| / traded value (log scale)
]

# Fundamentals from the latest *published* annual statement.
FUNDAMENTAL_FEATURES = [
    "earnings_yield",    # diluted EPS / price
    "book_to_price",     # book value per share / price
    "return_on_equity",  # net income / equity
    "debt_to_equity",    # total debt / equity
    "revenue_growth",    # fiscal-year revenue growth
    "log_market_cap",    # log(shares * price)
]

# Cross-sectional features computed within each trade date.
RELATIVE_FEATURES = [
    "sector_relative_momentum_3m",  # 3m momentum minus the industry mean that day
]

STATEMENT_COLUMNS = [
    "total_revenue", "net_income", "diluted_eps", "stockholders_equity",
    "total_debt", "shares_outstanding", "prev_total_revenue",
]

# Indian listed companies must publish audited annual results within 60 days of
# the fiscal year end (SEBI LODR, reg. 33); 90 days adds a safety margin.
STATEMENT_LAG_DAYS = 90


def add_timeseries_features(group: pd.DataFrame, index_returns: pd.Series) -> pd.DataFrame:
    """One symbol's daily rows (columns: trade_date, close, volume). `index_returns`
    is the benchmark index's daily return indexed by trade_date."""
    df = group.sort_values("trade_date").reset_index(drop=True)
    close = df["close"].astype(float)
    volume = df["volume"].astype(float)
    ret = close.pct_change()

    df["reversal_5d"] = close / close.shift(5) - 1.0
    df["momentum_12_1"] = close.shift(21) / close.shift(252) - 1.0
    df["volume_trend"] = (
        volume.rolling(5, min_periods=5).mean() / volume.rolling(60, min_periods=60).mean()
    )

    mkt = df["trade_date"].map(index_returns).astype(float)
    window, min_obs = 120, 60
    cov = ret.rolling(window, min_periods=min_obs).cov(mkt)
    var = mkt.rolling(window, min_periods=min_obs).var()
    beta = cov / var
    df["beta_120d"] = beta
    resid = ret - beta * mkt
    df["idio_vol_120d"] = resid.rolling(window, min_periods=min_obs).std()

    df["max_return_21d"] = ret.rolling(21, min_periods=21).max()
    traded_value = close * volume
    amihud = (ret.abs() / traded_value.where(traded_value > 0)).rolling(21, min_periods=21).mean()
    df["illiquidity_21d"] = np.log(amihud * 1e9 + 1e-12)
    return df.replace([np.inf, -np.inf], np.nan)


def add_fundamental_features(panel: pd.DataFrame, statements: pd.DataFrame) -> pd.DataFrame:
    """As-of join each (symbol, trade_date) to the latest statement published by
    then, and derive price-relative ratios with that day's close."""
    stmts = statements.copy()
    stmts["available_at"] = (
        pd.to_datetime(stmts["period_end"]) + pd.Timedelta(days=STATEMENT_LAG_DAYS)
    )
    merged = asof_join_fundamentals(
        panel, stmts, fund_time="available_at", columns=STATEMENT_COLUMNS,
        offset=pd.Timedelta(0),
    )
    close = merged["close"].astype(float)
    equity = merged["stockholders_equity"].where(merged["stockholders_equity"] > 0)
    shares = merged["shares_outstanding"].where(merged["shares_outstanding"] > 0)

    merged["earnings_yield"] = merged["diluted_eps"] / close
    merged["book_to_price"] = equity / shares / close
    merged["return_on_equity"] = merged["net_income"] / equity
    merged["debt_to_equity"] = merged["total_debt"] / equity
    prev_rev = merged["prev_total_revenue"].where(merged["prev_total_revenue"] > 0)
    merged["revenue_growth"] = merged["total_revenue"] / prev_rev - 1.0
    merged["log_market_cap"] = np.log(shares * close)
    merged = merged.drop(columns=STATEMENT_COLUMNS + ["available_at"], errors="ignore")
    return merged.replace([np.inf, -np.inf], np.nan)


def add_relative_features(cross_section: pd.DataFrame, industry_col: str = "industry") -> pd.DataFrame:
    """Within one trade date: momentum relative to the stock's industry."""
    out = cross_section.copy()
    mom = out["price_momentum_3m"]
    out["sector_relative_momentum_3m"] = mom - out.groupby(industry_col)[
        "price_momentum_3m"
    ].transform("mean")
    return out
