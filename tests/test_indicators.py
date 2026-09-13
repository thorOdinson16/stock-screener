"""
Unit tests for spark/jobs/indicators.py.

Run directly (no pytest needed):
    python tests/test_indicators.py
or with pytest:
    pytest tests/test_indicators.py
"""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(
    0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "spark", "jobs"))
)
from indicators import compute_features  # noqa: E402


def _frame(closes, symbol="TEST.NS", volume=1000):
    dates = pd.date_range("2020-01-01", periods=len(closes), freq="D").date
    return pd.DataFrame(
        {"symbol": symbol, "trade_date": dates, "close": closes, "volume": volume}
    )


def test_sma_linear_series():
    out = compute_features(_frame(list(range(1, 31))))
    assert abs(out["sma_20"].iloc[19] - (1 + 20) / 2) < 1e-9  # mean(1..20) = 10.5
    assert abs(out["sma_20"].iloc[29] - (11 + 30) / 2) < 1e-9  # mean(11..30) = 20.5
    assert np.isnan(out["sma_20"].iloc[18])  # warm-up
    assert np.isnan(out["sma_200"].iloc[29])  # not enough bars


def test_ema_and_macd_on_constant_series():
    out = compute_features(_frame([50.0] * 40))
    assert abs(out["ema_12"].iloc[-1] - 50.0) < 1e-9
    assert abs(out["ema_26"].iloc[-1] - 50.0) < 1e-9
    assert abs(out["macd"].iloc[-1]) < 1e-9
    assert abs(out["macd_signal"].iloc[-1]) < 1e-9


def test_rsi_all_gains_is_100():
    out = compute_features(_frame(list(range(1, 41))))
    assert abs(out["rsi_14"].iloc[20] - 100.0) < 1e-9


def test_rsi_all_losses_is_0():
    out = compute_features(_frame(list(range(40, 0, -1))))
    assert abs(out["rsi_14"].iloc[20] - 0.0) < 1e-9


def test_momentum_1m():
    closes = list(range(1, 31))
    out = compute_features(_frame(closes))
    # index 21: close 22 / close at index 0 (1) - 1 = 21
    assert abs(out["price_momentum_1m"].iloc[21] - (22.0 / 1.0 - 1.0)) < 1e-9
    assert np.isnan(out["price_momentum_1m"].iloc[20])


def test_distance_bounds_and_volume_ratio():
    out = compute_features(_frame(list(range(1, 60))))
    assert (out["distance_from_52w_high"].dropna() <= 0).all()
    assert (out["distance_from_52w_low"].dropna() >= 0).all()
    assert abs(out["volume_ratio"].iloc[-1] - 1.0) < 1e-9  # constant volume


def test_single_bar_does_not_raise():
    out = compute_features(_frame([10.0]))
    assert len(out) == 1
    assert np.isnan(out["sma_20"].iloc[0])


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
