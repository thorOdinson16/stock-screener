"""Run with api/.venv/bin/python -m unittest discover -s tests -p test_planner_api.py."""

from datetime import datetime, timezone, timedelta
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from api.planner import PlanRequest, Holding, trade_plan, quality
from api.druid import DruidError
from fastapi import HTTPException


class PlannerApiTests(unittest.TestCase):
    def setUp(self):
        self.today = datetime.now(timezone(timedelta(hours=5, minutes=30))).date().isoformat()
        self.row = {"symbol": "A.NS", "industry": "Sector", "trade_date": self.today, "close": 100.,
                    "volume": 1000000, "volume_ratio": 1., "volatility_20d": .02, "rank_21d": 1, "score_21d": .2}

    def test_plan_returns_whole_share_scenario(self):
        with patch("api.planner.query", return_value=[self.row]):
            result = trade_plan(PlanRequest())
        self.assertTrue(result["candidates"])
        self.assertGreaterEqual(result["cash_remaining"], 0)
        self.assertIsNone(result["probability"])

    def test_bad_settings_do_not_query_druid(self):
        with patch("api.planner.query") as query:
            with self.assertRaises(HTTPException) as error:
                trade_plan(PlanRequest(settings={"capital": -1}))
        self.assertEqual(error.exception.status_code, 422)
        query.assert_not_called()

    def test_unknown_horizon_is_rejected(self):
        with self.assertRaises(HTTPException) as error:
            trade_plan(PlanRequest(settings={"horizon": 63}))
        self.assertEqual(error.exception.status_code, 422)

    def test_service_error_has_actionable_message(self):
        with patch("api.planner.query", side_effect=DruidError("offline")):
            with self.assertRaises(HTTPException) as error:
                trade_plan(PlanRequest())
        self.assertEqual(error.exception.status_code, 503)

    def test_existing_industry_is_taken_from_served_metadata(self):
        with patch("api.planner.query", return_value=[self.row]):
            result = trade_plan(PlanRequest(settings={"max_sector_pct": 5}, holdings=[Holding(symbol="A", quantity=50, price=100, industry="Misspelled")] ))
        self.assertFalse(result["candidates"])
        self.assertIn("Already held", result["skipped"][0]["reasons"])

    def test_quality_does_not_claim_calibration(self):
        result = quality()
        self.assertFalse(result["probabilities_calibrated"])
        self.assertFalse(result["complex_actions_resolved"])


if __name__ == "__main__":
    unittest.main()
