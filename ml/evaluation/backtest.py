"""
backtest.py — cost-aware, non-overlapping long-only / long-short backtest.

The old "backtest" averaged overlapping forward returns with no costs, which
inflates a weak signal. Here positions are rebalanced every `horizon` trading
days (so forward-return windows do not overlap), a per-side cost is charged on
the fraction of each leg that changes, and net compounding/annualized
return/vol/Sharpe/max-drawdown are reported alongside the equal-weight universe
and benchmark index over the same rebalance dates.

Pure pandas/numpy so it is unit-testable (tests/test_features.py).
"""

import math

import numpy as np
import pandas as pd

from metrics import deflated_sharpe_ratio

DEFAULT_K = 20
DEFAULT_COST_BPS = 10.0
TRADING_DAYS_PER_YEAR = 252


def _turnover(previous, current) -> float:
    if previous is None:
        return 1.0
    if not current:
        return 0.0
    union = previous | current
    return 1.0 - len(previous & current) / len(union) if union else 0.0


def _ann_stats(net: pd.Series, horizon: int) -> dict:
    net = net.dropna()
    if net.empty:
        return {}
    periods_per_year = TRADING_DAYS_PER_YEAR / horizon
    equity = (1.0 + net).cumprod()
    std = net.std(ddof=1)
    sharpe = float(net.mean() / std * math.sqrt(periods_per_year)) if std > 0 else np.nan
    return {
        "periods": int(len(net)),
        "total_return": float(equity.iloc[-1] - 1.0),
        "annualized_return": float(equity.iloc[-1] ** (periods_per_year / len(net)) - 1.0),
        "annualized_vol": float(std * math.sqrt(periods_per_year)) if std == std else np.nan,
        "sharpe": sharpe,
        "max_drawdown": float((equity / equity.cummax() - 1.0).min()),
        "hit_rate": float((net > 0).mean()),
    }


def backtest(
    df: pd.DataFrame,
    horizon: int,
    k: int = DEFAULT_K,
    cost_bps: float = DEFAULT_COST_BPS,
    long_short: bool = True,
    ret_col: str | None = None,
    score_col: str = "score",
    symbol_col: str = "symbol",
    date_col: str = "trade_date",
    universe_ret_col: str | None = None,
    index_ret_col: str | None = None,
) -> dict:
    ret_col = ret_col or f"fwd_ret_{horizon}d"
    data = df.dropna(subset=[score_col, ret_col])
    dates = sorted(data[date_col].unique())
    if not dates:
        return {"long_short": long_short, "horizon": horizon, "k": k}

    rebalance = dates[::horizon]
    cost_rate = cost_bps / 1e4
    nets, grosses, costs, turnovers = [], [], [], []
    universe_rets, index_rets = [], []
    prev_top = prev_bottom = None
    used_dates = []

    for date in rebalance:
        group = data[data[date_col] == date]
        if len(group) < 2:
            continue
        top = group.nlargest(k, score_col)
        bottom = group.nsmallest(k, score_col)
        top_set, bottom_set = set(top[symbol_col]), set(bottom[symbol_col])

        gross = float(top[ret_col].mean())
        t_long = t_short = 0.0
        if long_short:
            gross -= float(bottom[ret_col].mean())
            t_short = _turnover(prev_bottom, bottom_set)
        t_long = _turnover(prev_top, top_set)
        # Each side (buy/sell) costs `cost_bps`; one-way turnover t means t sold
        # and t bought, hence 2*t sides per leg.
        cost = cost_rate * 2.0 * (t_long + t_short)

        grosses.append(gross)
        costs.append(cost)
        nets.append(gross - cost)
        turnovers.append(t_long + t_short)
        used_dates.append(date)
        if universe_ret_col and universe_ret_col in group:
            universe_rets.append(float(group[universe_ret_col].iloc[0]))
        if index_ret_col and index_ret_col in group:
            index_rets.append(float(group[index_ret_col].iloc[0]))

        prev_top, prev_bottom = top_set, bottom_set

    net = pd.Series(nets, index=used_dates)
    gross = pd.Series(grosses, index=used_dates)
    cost = pd.Series(costs, index=used_dates)
    result = {
        "long_short": long_short,
        "horizon": horizon,
        "k": k,
        "cost_bps": cost_bps,
        "avg_turnover": float(np.mean(turnovers)) if turnovers else np.nan,
        "avg_cost": float(cost.mean()) if not cost.empty else np.nan,
        "avg_gross_return": float(gross.mean()) if not gross.empty else np.nan,
    }
    result.update({f"net_{key}": val for key, val in _ann_stats(net, horizon).items()})
    result.update({f"gross_{key}": val for key, val in _ann_stats(gross, horizon).items()})
    result["deflated_sharpe"] = deflated_sharpe_ratio(net, n_trials=1)["dsr"]

    if universe_rets:
        result.update(
            {f"universe_{key}": val for key, val in _ann_stats(pd.Series(universe_rets), horizon).items()}
        )
    if index_rets:
        result.update(
            {f"index_{key}": val for key, val in _ann_stats(pd.Series(index_rets), horizon).items()}
        )
    return result
