"""
walk_forward.py — expanding-window walk-forward folds with an embargo.

A single train/test split gives one noisy estimate of a weak signal. Walk-forward
repeatedly trains on everything up to a point and tests on the next block, so
stability across folds can be assessed. An embargo of `embargo_days` between the
end of each training window and the start of its test window prevents a training
label (up to max(HORIZONS) days forward) from overlapping the test period.

Pure Python / no Spark so the fold logic is unit-testable; `run_walk_forward`
takes a `fit_predict(train_dates, test_dates) -> DataFrame` callable supplied by
the caller (ml/evaluation/evaluate.py wires the Spark model in).
"""

import pandas as pd

DEFAULT_N_FOLDS = 5
DEFAULT_MIN_TRAIN_FRACTION = 0.4
DEFAULT_TEST_FRACTION = 0.12
DEFAULT_EMBARGO_DAYS = 21


def expanding_folds(
    sorted_dates,
    n_folds: int = DEFAULT_N_FOLDS,
    min_train_fraction: float = DEFAULT_MIN_TRAIN_FRACTION,
    test_fraction: float = DEFAULT_TEST_FRACTION,
    embargo_days: int = DEFAULT_EMBARGO_DAYS,
):
    """Yields dicts with `train_dates` (expanding) and `test_dates` (contiguous
    blocks after the initial training window), separated by an embargo gap."""
    dates = list(sorted_dates)
    n = len(dates)
    if n < 8:
        return []

    min_train = max(2, int(round(n * min_train_fraction)))
    remaining = n - min_train
    block = max(1, int(round(n * test_fraction)))
    if block * n_folds > remaining and n_folds > 0:
        block = max(1, remaining // n_folds)

    folds = []
    test_start_idx = min_train
    while test_start_idx + block <= n and len(folds) < n_folds:
        train_end_idx = test_start_idx - embargo_days
        if train_end_idx >= 2:
            folds.append(
                {
                    "train_dates": [d for d in dates[:train_end_idx]],
                    "test_dates": [d for d in dates[test_start_idx:test_start_idx + block]],
                }
            )
        test_start_idx += block
    return folds


def run_walk_forward(dates, fit_predict, n_folds=DEFAULT_N_FOLDS, **kwargs) -> pd.DataFrame:
    """Concatenates the per-fold predictions returned by `fit_predict`."""
    frames = []
    for fold in expanding_folds(dates, n_folds=n_folds, **kwargs):
        pdf = fit_predict(fold["train_dates"], fold["test_dates"])
        if pdf is not None and not pdf.empty:
            frames.append(pdf)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)
