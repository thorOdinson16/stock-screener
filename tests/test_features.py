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

from features import add_forward_returns, assign_split, compute_split_dates  # noqa: E402
from metrics import (  # noqa: E402
    information_coefficient,
    precision_at_k,
    turnover,
)
from rules import rule_score  # noqa: E402
from indicators import compute_features  # noqa: E402


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
