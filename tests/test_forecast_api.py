"""API guards do not require a running stack or numerical training dependencies."""

from copy import deepcopy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from api.forecasts import stock_forecast, report, card_for_row
from fastapi import HTTPException


class ForecastApiTests(unittest.TestCase):
    def test_research_evidence_never_triggers_a_fake_live_forecast(self):
        artifact = {"source_status": "provisional", "horizons": {"21": {"production_ready": False}}}
        with patch("api.forecasts.read_artifact", return_value=artifact), patch("api.forecasts.query") as query:
            result = stock_forecast("RELIANCE", 21)
        query.assert_not_called()
        self.assertEqual(result["symbol"], "RELIANCE.NS")
        self.assertEqual(result["status"], "insufficient_evidence")
        self.assertIsNone(result["profit_probability"])

    def test_malformed_symbol_is_rejected(self):
        with self.assertRaises(HTTPException) as error:
            stock_forecast("A'; DROP TABLE screener", 21)
        self.assertEqual(error.exception.status_code, 422)

    def test_changed_corporate_evidence_is_labeled_stale(self):
        artifact = {"status": "research_only", "horizons": {}, "events_digest": "outdated"}
        with patch("api.forecasts.read_artifact", return_value=artifact):
            result = report()
        self.assertEqual(result["status"], "stale_evidence")
        self.assertTrue(all(e["terms_verified"] for e in result["entitlements"]))
        self.assertFalse(any(e["return_valuation_ready"] for e in result["entitlements"]))

    def test_missing_ranking_context_does_not_raise_or_create_numbers(self):
        artifact = {"source_status": "provisional", "horizons": {"21": {"production_ready": False}}}
        with patch("api.forecasts.lineage_matches", return_value=False):
            result = card_for_row(artifact, {"symbol": "A", "close": None}, 21, 0)
        self.assertEqual(result["status"], "insufficient_evidence")


if __name__ == "__main__":
    unittest.main()
