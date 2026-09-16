"""
Unit tests for monitoring/data_quality.py.

    python tests/test_data_quality.py
"""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "monitoring")))

from data_quality import find_duplicates, find_stale, robust_outliers  # noqa: E402


def test_find_duplicates():
    df = pd.DataFrame({"symbol": ["A", "B", "B", "C"]})
    assert find_duplicates(df) == ["B"]


def test_find_stale():
    df = pd.DataFrame({
        "symbol": ["A", "B", "C"],
        "trade_date": ["2026-01-10", "2026-01-10", "2025-12-20"],
    })
    assert find_stale(df, max_age_days=5) == ["C"]


def test_robust_outliers_flags_extreme_value():
    rng = np.random.default_rng(0)
    values = list(rng.normal(20, 2, 40)) + [500.0]
    df = pd.DataFrame({"symbol": [f"S{i}" for i in range(41)], "pe_ratio": values})
    flagged = robust_outliers(df, columns=["pe_ratio"], z_threshold=8.0)
    assert "pe_ratio" in flagged
    assert any(row["symbol"] == "S40" for row in flagged["pe_ratio"])


def test_robust_outliers_ignores_constant_column():
    df = pd.DataFrame({"symbol": ["A", "B", "C", "D"], "pe_ratio": [10.0] * 4})
    assert robust_outliers(df, columns=["pe_ratio"]) == {}


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
