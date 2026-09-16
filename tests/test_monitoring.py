"""
Unit tests for monitoring/collect_metrics.py parsers.

    python tests/test_monitoring.py
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "monitoring")))

from collect_metrics import parse_hdfs_report, total_kafka_lag  # noqa: E402

HDFS_REPORT = """\
Configured Capacity: 100000000000 (93.13 GB)
Present Capacity: 90000000000 (83.82 GB)
DFS Remaining: 50000000000 (46.57 GB)
DFS Used: 40000000000 (37.25 GB)
DFS Used%: 44.44%
Live datanodes (2):
Dead datanodes (1):
"""


def test_parse_hdfs_report():
    parsed = parse_hdfs_report(HDFS_REPORT)
    assert parsed["capacity_bytes"] == 100000000000
    assert parsed["used_bytes"] == 40000000000
    assert parsed["remaining_bytes"] == 50000000000
    assert parsed["live_datanodes"] == 2
    assert parsed["dead_datanodes"] == 1


def test_total_kafka_lag():
    text = (
        "seatunnel market.quotes 0 100 110 10 c1\n"
        "seatunnel market.quotes 1 50 60 10 c1\n"
    )
    assert total_kafka_lag(text) == 20


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
