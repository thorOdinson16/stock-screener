"""
Unit tests for druid/ingestion/supervisors.py.

    python tests/test_supervisors.py
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "druid", "ingestion")))

from supervisors import drained, load_ids  # noqa: E402


def test_load_ids_reads_datasources():
    with tempfile.TemporaryDirectory() as tmp:
        for name, ds in (("screener-kafka.json", "screener"), ("scores-kafka.json", "stock_scores")):
            with open(os.path.join(tmp, name), "w") as fh:
                json.dump({"dataSchema": {"dataSource": ds}}, fh)
        # a non-matching file must be ignored
        with open(os.path.join(tmp, "notes.json"), "w") as fh:
            json.dump({"foo": "bar"}, fh)
        assert set(load_ids(tmp)) == {"screener", "stock_scores"}


def test_drained_true_only_when_all_zero():
    assert drained([{"aggregateLag": 0}, {"aggregateLag": 0}])
    assert not drained([{"aggregateLag": 0}, {"aggregateLag": 5}])
    # unregistered supervisors (None) are ignored
    assert drained([None, {"aggregateLag": 0}])


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
