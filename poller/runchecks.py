"""
runchecks.py — small guards that make poller scripts fail loudly.

A poll that fetched nothing (e.g. Yahoo rate limiting) or lost messages on the
way to Kafka must not exit 0, otherwise the pipeline republishes stale data as
if it were fresh.
"""

import argparse
import logging

logger = logging.getLogger(__name__)

DEFAULT_MAX_FAILURE_FRACTION = 0.5


def positive_int(value: str) -> int:
    """argparse type: a whole number >= 1 (a negative limit would slice from the end)."""
    try:
        number = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"{value!r} is not a whole number") from error
    if number < 1:
        raise argparse.ArgumentTypeError("must be >= 1")
    return number


def fraction(value: str) -> float:
    """argparse type: a number in [0, 1]."""
    try:
        number = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"{value!r} is not a number") from error
    if not 0.0 <= number <= 1.0:
        raise argparse.ArgumentTypeError("must be between 0 and 1")
    return number


def require_success_rate(
    succeeded: int, attempted: int, what: str,
    max_failure_fraction: float = DEFAULT_MAX_FAILURE_FRACTION,
) -> None:
    """Exit non-zero when more than `max_failure_fraction` of `attempted` items failed."""
    if attempted <= 0:
        return
    failed = attempted - succeeded
    if failed / attempted > max_failure_fraction:
        raise SystemExit(
            f"{what}: {failed}/{attempted} failed (limit {max_failure_fraction:.0%}). "
            "Yahoo is probably rate limiting this IP; retry later."
        )
    if failed:
        logger.warning("%s: %d/%d failed (within the %.0f%% limit).",
                       what, failed, attempted, max_failure_fraction * 100)
