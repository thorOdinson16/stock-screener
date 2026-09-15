"""
rules.py — interpretable rule-based screening baseline (docs/project-spec.md §13).

Scoring is the count of matched technical screening conditions, so a higher score
means more conditions agree. Phase A uses only technical conditions because the
fundamental rules (P/E vs sector average) require point-in-time fundamentals,
which are introduced in Phase C.
"""

import pandas as pd


def rule_score(df: pd.DataFrame) -> pd.Series:
    momentum = (
        (df["close"] > df["sma_50"])
        & (df["sma_50"] > df["sma_200"])
        & (df["volume_ratio"] > 1.2)
    )
    breakout = df["distance_from_52w_high"] > -0.05
    rsi_ok = df["rsi_14"].between(40, 70)
    trend = df["price_momentum_1m"] > 0

    score = (
        momentum.astype(float)
        + breakout.astype(float)
        + rsi_ok.astype(float)
        + trend.astype(float)
    )
    return score
