"""
Unit tests for the poller's fail-loud guards (poller/runchecks.py and the
Kafka producer's delivery check).
    python tests/test_runchecks.py
or with pytest:
    pytest tests/test_runchecks.py
"""
import argparse
import os
import sys
import types

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(_ROOT, "poller"))

from runchecks import fraction, positive_int, require_success_rate  # noqa: E402


def _raises(exc, fn, *args):
    try:
        fn(*args)
    except exc:
        return True
    return False


def test_positive_int_rejects_zero_negative_and_text():
    assert positive_int("5") == 5
    for bad in ("0", "-5", "abc", "1.5", ""):
        assert _raises(argparse.ArgumentTypeError, positive_int, bad), bad


def test_fraction_bounds():
    assert fraction("0.5") == 0.5
    assert _raises(argparse.ArgumentTypeError, fraction, "1.5")
    assert _raises(argparse.ArgumentTypeError, fraction, "x")


def test_mass_failure_exits_nonzero_but_a_few_failures_do_not():
    require_success_rate(480, 500, "fetch")  # 4% failed: fine
    assert _raises(SystemExit, require_success_rate, 10, 500, "fetch")
    assert _raises(SystemExit, require_success_rate, 0, 500, "fetch")
    require_success_rate(0, 0, "fetch")  # nothing attempted: nothing to judge


class _FakeClient:
    """Minimal stand-in for the librdkafka producer."""

    def __init__(self):
        self.remaining = 0

    def produce(self, **kwargs):
        pass

    def poll(self, _timeout):
        pass

    def flush(self, _timeout):
        return self.remaining


def _make_producer():
    """MarketDataProducer wired to a fake client (works with or without the real one)."""
    stub = types.ModuleType("confluent_kafka")
    stub.Producer = lambda *a, **k: _FakeClient()
    injected = "confluent_kafka" not in sys.modules
    if injected:
        sys.modules["confluent_kafka"] = stub
    sys.modules.pop("kafka_producer", None)
    try:
        import kafka_producer
        producer = kafka_producer.MarketDataProducer()
        producer._producer = _FakeClient()
        return producer
    finally:
        if injected:
            sys.modules.pop("confluent_kafka", None)
        sys.modules.pop("kafka_producer", None)


def test_producer_raises_on_failed_or_undelivered_messages():
    producer = _make_producer()
    producer.flush()
    producer.raise_on_failure()  # clean run: no error
    producer._failed = 1
    assert _raises(RuntimeError, producer.raise_on_failure)
    producer._failed = 0
    producer._producer.remaining = 3
    producer.flush()
    assert _raises(RuntimeError, producer.raise_on_failure)


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
