"""
Unit tests for the benchmark helpers (benchmarks/common.py, recovery parsing).

Run directly (no pytest needed):
    python tests/test_benchmarks.py
or with pytest:
    pytest tests/test_benchmarks.py
"""

import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(_ROOT, "benchmarks"))

from common import percentile, stage_duration, stage_seconds  # noqa: E402
from recovery import completeness_checks, total_lag  # noqa: E402


def test_percentile_basic():
    values = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert abs(percentile(values, 0.5) - 3.0) < 1e-9
    assert abs(percentile(values, 0.0) - 1.0) < 1e-9
    assert abs(percentile(values, 1.0) - 5.0) < 1e-9
    assert abs(percentile(values, 0.95) - 4.8) < 1e-9
    assert percentile([], 0.5) != percentile([], 0.5)  # NaN


def test_stage_seconds():
    result = {"stages": [
        {"stage": "poll", "seconds": 1.5},
        {"stage": "score", "seconds": 2.0},
    ]}
    assert stage_seconds(result) == {"poll": 1.5, "score": 2.0}


def test_total_lag_parses_consumer_groups():
    text = (
        "GROUP            TOPIC           PARTITION  CURRENT-OFFSET  LOG-END-OFFSET  LAG  CONSUMER-ID\n"
        "seatunnel        market.quotes   0          100             110             10   c1\n"
        "seatunnel        market.quotes   1          50              60              10   c1\n"
        "druid            market.scores   0          5               25              20   -\n"
    )
    lag, rows = total_lag(text)
    assert lag == 40
    assert rows == 3


def test_total_lag_ignores_header_and_bad_lines():
    text = "GROUP TOPIC PARTITION CURRENT-OFFSET LOG-END-OFFSET LAG\ngarbage line\n"
    assert total_lag(text) == (0, 0)


def test_stage_duration_reads_one_stage():
    result = {"stages": [{"stage": "poll", "seconds": 46.4}, {"stage": "wait_druid", "seconds": 0.1}]}
    assert stage_duration(result, "wait_druid") == 0.1  # not the poll time
    assert stage_duration(result, "missing") is None


def test_recovery_is_not_verified_when_lag_remains_or_nothing_was_served():
    before = {"kafka": {"lag": 5333}, "druid": {}}
    ok = completeness_checks(before, {"kafka": {"lag": 0}}, {"screener": 499, "stock_scores": 980})
    assert ok["problems"] == []
    stuck = completeness_checks(before, {"kafka": {"lag": 5333}}, {"screener": 499, "stock_scores": 980})
    assert any("lag" in p for p in stuck["problems"])
    empty = completeness_checks(before, {"kafka": {"lag": 0}}, {"screener": 0, "stock_scores": None})
    assert len(empty["problems"]) == 2


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
