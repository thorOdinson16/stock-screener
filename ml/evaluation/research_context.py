"""Past-only market/sector features and portfolio robustness diagnostics."""

import numpy as np
import pandas as pd

CONTEXT_FEATURES = ["market_return_1d", "market_return_21d", "market_return_63d", "market_trend_200d",
                    "market_volatility_20d", "sector_momentum_63d", "sector_relative_63d", "beta_63d"]


def add_context(frame, bars, sessions, statistics):
    result = frame.copy()
    market = pd.DataFrame({"trade_date": sessions, "index_close": [bars[("^NSEI", d)]["close"] for d in sessions]})
    market["market_return_1d"] = market.index_close.pct_change(fill_method=None)
    for h in (21, 63):
        market[f"market_return_{h}d"] = market.index_close.pct_change(h, fill_method=None)
    market["market_trend_200d"] = market.index_close / market.index_close.rolling(200, min_periods=200).mean() - 1
    market["market_volatility_20d"] = market.market_return_1d.rolling(20, min_periods=20).std()
    result = result.merge(market.drop(columns="index_close"), on="trade_date", how="left", validate="many_to_one")
    result["_momentum"] = [statistics.get((s, d), {}).get("momentum") for s, d in zip(result.symbol, result.trade_date)]
    groups = result.groupby(["trade_date", "industry"], dropna=False)._momentum
    total, count = groups.transform("sum"), groups.transform("count")
    result["sector_momentum_63d"] = ((total - result._momentum) / (count - 1)).where(count >= 3)
    result["sector_relative_63d"] = result._momentum - result.sector_momentum_63d
    result["beta_63d"] = np.nan
    index_returns = market.set_index("trade_date").market_return_1d
    for symbol, part in result.groupby("symbol", sort=False):
        closes = pd.Series([bars.get((symbol, d), {}).get("close") for d in sessions], index=sessions, dtype=float)
        returns = closes.pct_change(fill_method=None)
        beta = returns.rolling(63, min_periods=63).cov(index_returns) / index_returns.rolling(63, min_periods=63).var()
        result.loc[part.index, "beta_63d"] = part.trade_date.map(beta).to_numpy()
    return result.drop(columns="_momentum")


def portfolio_robustness(simulation, market_context, cutoff):
    curve = pd.DataFrame(simulation["curve"])
    trades = pd.DataFrame(simulation["trades"])
    if curve.empty:
        return {"status": "unavailable", "reason": "No valued portfolio dates"}
    curve["daily_return"] = curve.equity / curve.equity.shift(1).fillna(simulation["policy"]["capital"]) - 1
    curve["exposure"] = (curve.equity - curve.cash) / curve.equity
    context = market_context[["trade_date", "market_return_1d", "market_trend_200d", "market_volatility_20d"]].drop_duplicates("trade_date")
    curve = curve.merge(context, left_on="date", right_on="trade_date", how="left", validate="one_to_one")
    valid = curve.dropna(subset=["market_return_1d", "daily_return"])
    beta = float(valid.daily_return.cov(valid.market_return_1d) / valid.market_return_1d.var()) if len(valid) > 20 and valid.market_return_1d.var() > 0 else None
    curve["regime"] = np.where(curve.market_trend_200d >= 0, "market_above_200d", "market_below_200d")
    curve["volatility_regime"] = np.where(curve.market_volatility_20d > cutoff, "higher_volatility", "lower_volatility")
    def groups(column):
        return {str(key): {"dates": len(part), "compounded_daily_contribution": float((1 + part.daily_return).prod() - 1),
                           "mean_exposure": float(part.exposure.mean())} for key, part in curve.groupby(column)}
    by_symbol = trades.groupby("symbol").pnl.sum().sort_values(ascending=False) if len(trades) else pd.Series(dtype=float)
    positive = by_symbol.clip(lower=0)
    return {"status": "complete" if simulation["status"] == "complete" else "partial_prefix",
            "mean_cash_exposure": float(1 - curve.exposure.mean()), "market_beta": beta,
            "mean_daily_market_residual": float((valid.daily_return - beta * valid.market_return_1d).mean()) if beta is not None else None,
            "turnover_on_initial_capital": float((trades.cost.sum() + trades.proceeds.sum()) / simulation["policy"]["capital"]) if len(trades) else 0.,
            "positive_pnl_top_stock_share": float(positive.iloc[0] / positive.sum()) if positive.sum() else None,
            "positive_pnl_top_five_share": float(positive.head(5).sum() / positive.sum()) if positive.sum() else None,
            "stock_pnl": {str(s): float(v) for s, v in by_symbol.items()},
            "industry_pnl": {str(s): float(v) for s, v in trades.groupby("industry").pnl.sum().items()} if len(trades) else {},
            "quarters": {str(q): {"dates": len(p), "return": float((1 + p.daily_return).prod() - 1)} for q, p in curve.groupby(pd.to_datetime(curve.date).dt.to_period("Q").astype(str))},
            "market_regimes": groups("regime"), "volatility_regimes": groups("volatility_regime"),
            "limits": ["Regime-conditioned compounding is attribution, not a separately tradable portfolio.",
                       "Market beta/residuals are descriptive estimates, not proven alpha.",
                       "Incomplete portfolios expose only their valued prefix and closed-trade contributions."]}
