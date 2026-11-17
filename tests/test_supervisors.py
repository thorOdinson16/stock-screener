"""
Unit tests for druid/ingestion/supervisors.py.

    python tests/test_supervisors.py
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "druid", "ingestion")))

import supervisors  # noqa: E402
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


def _with_request(result, fn):
    original = supervisors._request
    supervisors._request = lambda *a, **k: result
    try:
        return fn()
    finally:
        supervisors._request = original


def test_action_fails_when_druid_is_unreachable():
    ok, message = _with_request((None, "connection refused"),
                                lambda: supervisors._action("screener", "suspend", "http://x"))
    assert not ok and "unreachable" in message


def test_action_fails_on_http_error():
    ok, _ = _with_request((500, "boom"), lambda: supervisors._action("screener", "resume", "http://x"))
    assert not ok


def test_action_tolerates_unregistered_supervisor():
    ok, message = _with_request((404, "nope"), lambda: supervisors._action("screener", "suspend", "http://x"))
    assert ok and "not registered" in message


def test_action_accepts_success_codes():
    for code in (200, 202):
        ok, _ = _with_request((code, None), lambda: supervisors._action("screener", "suspend", "http://x"))
        assert ok


def test_main_returns_nonzero_when_any_supervisor_fails():
    original_argv = sys.argv
    sys.argv = ["supervisors.py", "suspend", "--ids", "a", "b"]
    try:
        assert _with_request((None, "down"), supervisors.main) == 1
        assert _with_request((200, None), supervisors.main) == 0
    finally:
        sys.argv = original_argv


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
