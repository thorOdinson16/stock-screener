from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path
import random
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ml.evaluation.calibration import (fit_probability, probability, assess_probability, build_artifact,
                                     forecast, weighted_quantile, return_band, block_interval)
from ml.evaluation.forecast_outcomes import outcome
from ml.evaluation.retail import Policy
from ml.evaluation.entitlements import value_position


class ForecastTests(unittest.TestCase):
    def test_next_close_stop_outcome_and_costs(self):
        days = ["2026-01-01", "2026-01-02", "2026-01-05"]
        bars = {("A", days[0]): {"close": 100.},
                ("A", days[1]): {"close": 90., "open": 90., "low": 80.},
                ("A", days[2]): {"close": 90., "open": 90., "low": 89.}}
        result = outcome("A", 0, 2, bars, days, [], Policy())
        expected = 90 * .999 * .999 / (100 * 1.001 * 1.001) - 1
        self.assertAlmostEqual(result["net_return"], expected)
        self.assertEqual(result["actual_exit_date"], days[1])
        self.assertEqual(result["scheduled_exit_date"], days[2])

    def test_future_listing_price_cannot_value_unlisted_entitlement(self):
        event = {"id": "e", "impact_start": "2026-01-05", "ex_date": "2026-01-05", "parent_asset_id": "parent",
                 "terms_verified": True, "terms_source": "official", "entitlements": [
                     {"asset_id": "child", "ratio": 1, "listing_date": "2026-02-01", "listing_source": "exchange"}]}
        quotes = {("parent", "2026-01-01"): {"close": 100., "basis": "unadjusted", "currency": "INR", "source": "exchange"},
                  ("parent", "2026-01-10"): {"close": 40., "basis": "unadjusted", "currency": "INR", "source": "exchange"},
                  ("child", "2026-01-10"): {"close": 60., "basis": "unadjusted", "currency": "INR", "source": "incorrect backfill"}}
        result = value_position(event, "2026-01-01", "2026-01-10", quotes)
        self.assertEqual(result["status"], "incomplete")
        self.assertTrue(any("listing" in r["reason"] for r in result["missing"]))

    def test_complete_entitlements_require_exact_window_reconciliation(self):
        event = {"impact_start": "2026-01-05", "ex_date": "2026-01-05", "parent_asset_id": "parent",
                 "terms_verified": True, "terms_source": "official", "entitlements": [
                     {"asset_id": "child", "ratio": 1, "listing_date": "2026-01-06", "listing_source": "exchange"}]}
        quote = lambda close: {"close": close, "basis": "unadjusted", "currency": "INR", "source": "exchange"}
        quotes = {("parent", "2026-01-01"): quote(100.), ("parent", "2026-01-10"): quote(40.), ("child", "2026-01-10"): quote(60.)}
        review = {"entry_date": "2026-01-01", "exit_date": "2026-01-10", "cash_per_original_share": 0., "no_other_share_changes": True, "source": "reviewed official actions"}
        self.assertEqual(value_position(event, "2026-01-01", "2026-01-10", quotes, review)["gross_return"], 0.)
        review["exit_date"] = "2026-01-11"
        self.assertEqual(value_position(event, "2026-01-01", "2026-01-10", quotes, review)["status"], "incomplete")

    def test_immature_cases_are_not_missing_matured_outcomes_and_fit_is_purged(self):
        cases = []
        for horizon in (21, 63):
            for fold, day, end in [(1, "2024-01-01", "2024-02-01"), (2, "2024-03-01", "2024-05-20"),
                                   (3, "2024-04-01", "2024-05-01"), (4, "2024-07-01", "2024-08-01")]:
                cases.append({"horizon": horizon, "fold": fold, "signal_date": day, "scheduled_exit_date": end,
                              "actual_exit_date": day, "eligible": True, "status": "complete", "rank": 1,
                              "rank_percentile": 1., "volatility": .02, "profit": 1., "outperform": 1., "net_return": .01})
            cases.append({"horizon": horizon, "fold": 5, "signal_date": "2025-01-01", "scheduled_exit_date": "", "eligible": True, "status": "immature"})
        audit = {"last_completed_session": "2024-12-31", "status": "provisional", "price_basis": "provider_adjusted",
                 "outcomes_sha256": "test", "events_digest": "test", "policy": {}}
        with patch("ml.evaluation.calibration.fit_probability", return_value=None) as fitted:
            artifact = build_artifact(cases, audit)
        for call in fitted.call_args_list:
            self.assertEqual([r["fold"] for r in call.args[0]], [1])
        self.assertEqual(artifact["horizons"]["21"]["matured_missing_fraction"], 0.)

    def test_research_artifact_never_becomes_live_confidence(self):
        artifact = {"source_status": "provisional", "horizons": {"21": {"production_ready": False}}}
        result = forecast(artifact, 21)
        self.assertEqual(result["status"], "insufficient_evidence")
        self.assertIsNone(result["profit_probability"])
        self.assertIsNone(result["exit_value_band"])

    def test_range_can_be_available_without_fabricating_probability(self):
        model = {"features": ["rank_percentile", "volatility"], "mean": [1., .02], "scale": [.1, .01], "coefficients": [0., 1., 0.]}
        artifact = {"source_status": "verified", "horizons": {"21": {"production_ready": True,
            "probability_models": {"profit": model}, "probability_metrics": {"profit": {"passed": False}, "outperform": {"passed": False}},
            "range_metrics": {"passed": True}, "range_model": {"nominal_coverage": .8, "expansion": 0.,
                "bands": {"0": {"low": -.1, "median": .02, "high": .2}}}}}}
        result = forecast(artifact, 21, {"rank": 1, "rank_percentile": 1., "volatility": .02}, 100., {"fresh": True, "lineage_matches": True})
        self.assertEqual(result["status"], "partial")
        self.assertIsNone(result["profit_probability"])
        self.assertAlmostEqual(result["exit_value_band"]["high"], 120.)

    def test_weighted_quantile_and_small_block_sample(self):
        self.assertEqual(weighted_quantile([1, 100], [.99, .01], .5), 1.)
        self.assertIsNone(block_interval([0.] * 63, 63)["high"])

    def test_logistic_is_measured_on_later_dates(self):
        import math
        rng = random.Random(42)
        def sample(start, count):
            rows = []
            for i in range(count):
                day = (date.fromisoformat(start) + timedelta(days=i)).isoformat()
                for rank in range(1, 21):
                    x = (10.5 - rank) / 5.77
                    y = int(rng.random() < 1 / (1 + math.exp(-2 * x)))
                    rows.append({"signal_date": day, "rank_percentile": 1 - (rank - 1) / 500, "volatility": .02, "profit": y})
            return rows
        model = fit_probability(sample("2020-01-01", 300), "profit")
        result = assess_probability(model, sample("2022-01-01", 300), "profit", 21)
        self.assertLess(result["brier"], result["baseline_brier"])
        self.assertTrue(result["passed"])


if __name__ == "__main__":
    unittest.main()
