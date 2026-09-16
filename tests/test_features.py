"""
Unit tests for the ML feature/label/metrics/rules layer:
  ml/feature_engineering/features.py
  ml/evaluation/metrics.py
  ml/evaluation/rules.py

Run directly (no pytest needed):
    python tests/test_features.py
or with pytest:
    pytest tests/test_features.py
"""

import os
import sys

import numpy as np
import pandas as pd

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(_ROOT, "ml", "feature_engineering"))
sys.path.insert(0, os.path.join(_ROOT, "ml", "evaluation"))
sys.path.insert(0, os.path.join(_ROOT, "spark", "jobs"))

from features import (  # noqa: E402
    add_excess_returns,
    add_forward_returns,
    assign_split,
    compute_split_dates,
)
from metrics import (  # noqa: E402
    deflated_sharpe_ratio,
    information_coefficient,
    newey_west_tstat,
    precision_at_k,
    turnover,
)
from rules import rule_score  # noqa: E402
from indicators import compute_features  # noqa: E402
from transform import (  # noqa: E402
    MODEL_FEATURES,
    add_derived_features,
    add_regime_features,
    apply_cross_sectional,
    transform_group,
)
from walk_forward import expanding_folds  # noqa: E402
from backtest import backtest  # noqa: E402
from asof import asof_join_fundamentals  # noqa: E402


def _frame(closes, symbol="TEST.NS", start="2020-01-01"):
    dates = pd.date_range(start, periods=len(closes), freq="D").date
    return pd.DataFrame(
        {"symbol": symbol, "trade_date": list(dates), "close": closes, "volume": 1000}
    )


def test_forward_return_values():
    out = add_forward_returns(_frame(list(range(1, 31))))
    # index 0: close[5]/close[0] - 1 = 6/1 - 1
    assert abs(out["fwd_ret_5d"].iloc[0] - (6.0 / 1.0 - 1.0)) < 1e-12
    # index 8: close[29]/close[8] - 1 = 30/9 - 1
    assert abs(out["fwd_ret_21d"].iloc[8] - (30.0 / 9.0 - 1.0)) < 1e-12


def test_forward_return_tail_nan():
    out = add_forward_returns(_frame(list(range(1, 31))))
    assert np.isnan(out["fwd_ret_5d"].iloc[-1])
    assert out["fwd_ret_5d"].notna().sum() == 25  # 30 - 5
    assert out["fwd_ret_21d"].notna().sum() == 9  # 30 - 21


def test_compute_split_dates():
    dates = list(range(100))
    test_start, embargo_start = compute_split_dates(dates, test_fraction=0.2, embargo_days=21)
    assert test_start == 80
    assert embargo_start == 59


def test_assign_split_embargo():
    df = pd.DataFrame({"trade_date": list(range(100))})
    out = assign_split(df, list(range(100)), test_fraction=0.2, embargo_days=21)
    assert out.loc[out["trade_date"] == 59, "split"].iloc[0] == "embargo"
    assert out.loc[out["trade_date"] == 79, "split"].iloc[0] == "embargo"
    assert out.loc[out["trade_date"] == 58, "split"].iloc[0] == "train"
    assert out.loc[out["trade_date"] == 80, "split"].iloc[0] == "test"


def test_features_are_past_only():
    closes = [float(c) for c in range(1, 61)]
    full = compute_features(_frame(closes))
    truncated = compute_features(_frame(closes[:50]))
    cols = ["sma_20", "rsi_14", "macd", "volatility_20d", "price_momentum_1m"]
    assert np.allclose(
        full[cols].iloc[49].values, truncated[cols].iloc[49].values, equal_nan=True
    )


def test_ic_perfect_and_inverted():
    df = pd.DataFrame(
        {
            "trade_date": [1, 1, 2, 2],
            "score": [1.0, 2.0, 1.0, 2.0],
            "ret": [0.01, 0.02, 0.03, 0.04],
        }
    )
    assert abs(information_coefficient(df, "score", "ret")["ic_mean"] - 1.0) < 1e-9

    df["ret"] = [-0.01, -0.02, -0.03, -0.04]
    assert abs(information_coefficient(df, "score", "ret")["ic_mean"] + 1.0) < 1e-9


def test_precision_at_k():
    df = pd.DataFrame(
        {
            "trade_date": [1, 1, 1],
            "symbol": ["A", "B", "C"],
            "score": [3.0, 2.0, 1.0],
            "ret": [0.05, -0.01, 0.10],
            "bench": [0.0, 0.0, 0.0],
        }
    )
    # top-1 by score is A, which beat benchmark -> precision 1.0
    assert abs(precision_at_k(df, "score", "ret", k=1, benchmark_col="bench")["precision_at_k"] - 1.0) < 1e-9
    # top-2: A wins, B loses -> 0.5
    assert abs(precision_at_k(df, "score", "ret", k=2, benchmark_col="bench")["precision_at_k"] - 0.5) < 1e-9


def test_turnover():
    df = pd.DataFrame(
        {
            "trade_date": [1, 1, 2, 2],
            "symbol": ["A", "B", "A", "C"],
            "score": [2.0, 1.0, 2.0, 1.0],
        }
    )
    # top-1 {A} -> {A}: no change
    assert abs(turnover(df, "score", k=1) - 0.0) < 1e-9


def test_rule_score_bounds():
    df = pd.DataFrame(
        {
            "close": [100.0],
            "sma_50": [90.0],
            "sma_200": [80.0],
            "volume_ratio": [1.5],
            "distance_from_52w_high": [-0.01],
            "rsi_14": [55.0],
            "price_momentum_1m": [0.02],
        }
    )
    score = rule_score(df)
    assert score.iloc[0] == 4.0  # all four conditions matched


def _feature_panel(symbols=("A", "B"), periods=3, scale=1.0):
    """Raw-indicator panel. `scale` multiplies every price-level column so the
    derived ratios must be invariant to it."""
    rows = []
    for sym in symbols:
        for i in range(periods):
            price = lambda offset=0.0: (100.0 + i + offset) * scale
            rows.append(
                {
                    "symbol": sym,
                    "trade_date": f"2020-01-{i + 1:02d}",
                    "close": price(0),
                    "sma_20": price(-1),
                    "sma_50": price(-2),
                    "sma_200": price(-3),
                    "ema_12": price(-1),
                    "ema_26": price(-2),
                    "macd": 0.5 * scale,
                    "macd_signal": 0.25 * scale,
                    "rsi_14": 50.0 + i,
                    "volatility_20d": 0.02,
                    "volume_ratio": 1.0 + i * 0.1,
                    "distance_from_52w_high": -0.1 - i * 0.01,
                    "distance_from_52w_low": 0.2 + i * 0.01,
                    "price_momentum_1m": 0.01 * i,
                    "price_momentum_3m": 0.02 * i,
                    "price_momentum_6m": 0.03 * i,
                }
            )
    return pd.DataFrame(rows)


def test_derived_features_are_scale_free():
    small = transform_group(_feature_panel(scale=1.0))
    large = transform_group(_feature_panel(scale=10.0))
    for col in MODEL_FEATURES:
        assert np.allclose(
            small[col].values, large[col].values, equal_nan=True
        ), f"{col} depends on price scale"


def test_regime_features_present_and_interact():
    out = transform_group(_feature_panel(symbols=("A", "B", "C"), periods=1))
    for col in ("market_breadth", "market_volatility", "momentum_x_breadth",
                "volatility_x_market"):
        assert col in out.columns
    # market breadth is constant within the date -> normalizes to zero
    assert np.allclose(out["market_breadth"].values, 0.0)


def test_cross_sectional_zscore_mean_zero():
    panel = _feature_panel(symbols=("A", "B", "C"), periods=2)
    out = apply_cross_sectional(add_regime_features(add_derived_features(panel)))
    means = out.groupby("trade_date")[MODEL_FEATURES].mean().abs()
    assert (means.values < 1e-9).all()


def test_cross_sectional_rank_bounds_and_order():
    panel = _feature_panel(symbols=("A", "B", "C"), periods=1)
    derived = add_regime_features(add_derived_features(panel))
    out = apply_cross_sectional(derived, method="rank")
    values = out[MODEL_FEATURES].values
    assert (values >= -0.5).all() and (values <= 0.5).all()
    # rsi_14 increases with the symbol's row index -> preserved order after ranking
    order = np.argsort(derived["rsi_14"].values)
    ranked = out["rsi_14"].values
    assert list(order) == list(np.argsort(ranked))


def test_excess_returns_mean_zero_per_date():
    df = _feature_panel(symbols=("A", "B", "C"), periods=1)
    df["fwd_ret_5d"] = [0.01, 0.02, 0.03]
    df["fwd_ret_21d"] = [0.05, -0.01, 0.02]
    out = add_excess_returns(df)
    for col in ("excess_ret_5d", "excess_ret_21d"):
        assert abs(out[col].mean()) < 1e-12


def test_transform_train_serve_parity_per_date():
    """Scoring sees one cross-section; normalizing a single date must match the
    same date inside the full panel (no cross-date dependence)."""
    panel = _feature_panel(symbols=("A", "B", "C", "D"), periods=4)
    full = pd.concat([transform_group(g) for _, g in panel.groupby("trade_date")])
    for date, group in panel.groupby("trade_date"):
        single = transform_group(group)
        full_date = full[full["trade_date"] == date].sort_values("symbol").reset_index(drop=True)
        single = single.sort_values("symbol").reset_index(drop=True)
        assert np.allclose(full_date[MODEL_FEATURES].values, single[MODEL_FEATURES].values)


def test_walk_forward_embargo_and_expanding():
    folds = expanding_folds(list(range(100)), n_folds=5, min_train_fraction=0.4,
                            test_fraction=0.12, embargo_days=21)
    assert folds
    prev_train = -1
    for fold in folds:
        assert max(fold["train_dates"]) < min(fold["test_dates"])
        assert min(fold["test_dates"]) - max(fold["train_dates"]) >= 21
        assert len(fold["train_dates"]) > prev_train  # expanding
        prev_train = len(fold["train_dates"])


def test_backtest_cost_math():
    df = pd.DataFrame(
        {
            "trade_date": ["2020-01-01", "2020-01-01"],
            "symbol": ["A", "B"],
            "score": [2.0, 1.0],
            "fwd_ret_5d": [0.10, 0.0],
        }
    )
    result = backtest(df, horizon=5, k=1, cost_bps=10.0, long_short=False)
    assert abs(result["avg_gross_return"] - 0.10) < 1e-12
    # opening a position = one-way turnover 1.0 -> 2 sides * 10bps
    assert abs(result["avg_cost"] - 0.002) < 1e-12
    assert abs(result["net_total_return"] - 0.098) < 1e-12


def test_backtest_long_short_spread():
    df = pd.DataFrame(
        {
            "trade_date": ["2020-01-01", "2020-01-01"],
            "symbol": ["A", "B"],
            "score": [2.0, 1.0],
            "fwd_ret_5d": [0.10, -0.04],
        }
    )
    result = backtest(df, horizon=5, k=1, cost_bps=0.0, long_short=True)
    assert abs(result["avg_gross_return"] - 0.14) < 1e-12


def test_newey_west_significance():
    rng = np.random.default_rng(0)
    strong = newey_west_tstat(0.05 + rng.normal(0, 0.001, 100), lags=4)
    assert strong["mean"] > 0.04
    assert strong["t_stat"] > 5
    noisy = newey_west_tstat(rng.normal(0, 1, 200), lags=4)
    assert abs(noisy["t_stat"]) < 3


def test_deflated_sharpe_penalizes_trials():
    rng = np.random.default_rng(1)
    returns = 0.001 + rng.normal(0, 0.01, 250)
    one = deflated_sharpe_ratio(returns, n_trials=1)
    many = deflated_sharpe_ratio(returns, n_trials=100)
    assert 0.0 <= one["dsr"] <= 1.0
    assert one["dsr"] >= many["dsr"]


def test_asof_join_picks_latest_known_snapshot():
    panel = pd.DataFrame(
        {"symbol": ["A", "A"], "trade_date": ["2020-01-05", "2020-01-15"]}
    )
    fundamentals = pd.DataFrame(
        {
            "symbol": ["A", "A"],
            "timestamp": ["2020-01-03 09:00:00", "2020-01-10 09:00:00"],
            "pe_ratio": [10.0, 20.0],
        }
    )
    out = asof_join_fundamentals(panel, fundamentals).sort_values("trade_date")
    assert list(out["pe_ratio"]) == [10.0, 20.0]


def test_asof_join_no_lookahead():
    panel = pd.DataFrame({"symbol": ["A"], "trade_date": ["2020-01-05"]})
    # A snapshot published the day *after* the trade date must not be used.
    fundamentals = pd.DataFrame(
        {
            "symbol": ["A"],
            "timestamp": ["2020-01-06 09:00:00"],
            "pe_ratio": [99.0],
        }
    )
    out = asof_join_fundamentals(panel, fundamentals)
    assert np.isnan(out["pe_ratio"].iloc[0])


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failures = 0
    for test in tests:
        try:
            test()
            print(f"PASS {test.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"FAIL {test.__name__}: {e}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(_run_all())
