"""Skip helpers for tests that need heavy optional libraries (torch, xgboost, ...)."""
import importlib.util
import unittest


def missing(*modules):
    return [m for m in modules if importlib.util.find_spec(m) is None]


def requires(*modules):
    """Decorator: skip a test/class when any of `modules` is not installed."""
    absent = missing(*modules)
    return unittest.skipIf(bool(absent), "requires " + ", ".join(absent))


def require_module_or_skip(*modules):
    """Call at import time to skip a whole test module."""
    absent = missing(*modules)
    if absent:
        raise unittest.SkipTest("requires " + ", ".join(absent))
