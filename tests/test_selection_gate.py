"""
Unit tests for ml/evaluation/selection_gate.py (the retrain safety floor).
    python tests/test_selection_gate.py
"""
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(_ROOT, "ml", "evaluation"))

from selection_gate import gate_failures, label_failures  # noqa: E402


def _results(ic, walk_forward_ic=None, error=None):
    label = {"rank_ensemble": {"ic_mean": ic}}
    if walk_forward_ic is not None or error:
        label["walk_forward"] = {"ic_mean": walk_forward_ic, **({"error": error} if error else {})}
    return {"labels": {"excess_ret_21d": label}}


SELECTED = {"labels": {"excess_ret_21d": {"model_name": "rank_ensemble"}}}


def test_positive_walk_forward_ic_passes():
    assert gate_failures(_results(0.01, walk_forward_ic=0.02), SELECTED) == []


def test_walk_forward_ic_overrides_a_flattering_fixed_test():
    failures = gate_failures(_results(0.05, walk_forward_ic=-0.01), SELECTED)
    assert failures and "walk-forward" in failures[0]


def test_fixed_test_ic_is_used_when_walk_forward_is_missing_or_errored():
    assert gate_failures(_results(0.03), SELECTED) == []
    assert gate_failures(_results(-0.03), SELECTED)
    assert gate_failures(_results(-0.03, walk_forward_ic=None, error="boom"), SELECTED)


def test_zero_nan_and_missing_ic_fail():
    assert gate_failures(_results(0.0), SELECTED)
    assert gate_failures(_results(float("nan")), SELECTED)
    assert label_failures("x", {}, "missing_model")


def test_every_label_must_pass():
    results = {"labels": {"a": {"m": {"ic_mean": 0.02}}, "b": {"m": {"ic_mean": -0.02}}}}
    selected = {"labels": {"a": {"model_name": "m"}, "b": {"model_name": "m"}}}
    failures = gate_failures(results, selected)
    assert len(failures) == 1 and failures[0].startswith("b:")


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
