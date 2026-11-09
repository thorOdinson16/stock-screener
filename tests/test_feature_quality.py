"""Run with the Spark Python environment; no live services required."""

from pathlib import Path
import sys
import unittest

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "spark/jobs"), str(ROOT / "ml/feature_engineering")]
from ml.evaluation.corporate_actions import reset_features, mask_labels
from indicators import compute_features
from features import compute_labels


class FeatureQualityTests(unittest.TestCase):
    def setUp(self):
        dates = pd.date_range("2025-01-01", periods=300).date
        self.frame = pd.DataFrame({"symbol": "A", "trade_date": dates, "close": 100., "volume": 1000})
        self.event = {"symbols": ["A"], "impact_start": str(dates[250]), "impact_end": str(dates[251])}

    def test_indicators_reset_after_a_structural_action(self):
        self.frame.loc[252:, "close"] = 40.
        result = reset_features(self.frame, [self.event], compute_features)
        self.assertEqual(len(result), 300)
        self.assertAlmostEqual(result.iloc[249]["sma_200"], 100.)
        self.assertTrue(pd.isna(result.iloc[250]["ema_12"]))
        self.assertEqual(result.iloc[252]["close"], 40.)
        self.assertEqual(result.iloc[252]["ema_12"], 40.)
        self.assertTrue(pd.isna(result.iloc[299]["sma_200"]))

    def test_cross_action_labels_are_unknown_not_negative_returns(self):
        self.frame.loc[250:, "close"] = 40.
        result = mask_labels(self.frame, [self.event], (5, 21), compute_labels)
        self.assertTrue(pd.isna(result.iloc[245]["fwd_ret_5d"]))
        self.assertTrue(pd.isna(result.iloc[251]["fwd_ret_21d"]))
        self.assertEqual(result.iloc[252]["fwd_ret_21d"], 0.)
        self.assertEqual(result.iloc[200]["fwd_ret_21d"], 0.)


if __name__ == "__main__":
    unittest.main()
