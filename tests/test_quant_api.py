"""Quant diagnostics stay dependency-light and never query live market data."""

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from api.planner import equations
from ml.evaluation.quant_report import read_report


class QuantApiTests(unittest.TestCase):
    def test_missing_report_does_not_claim_evidence_or_model_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            result = read_report(directory)
        self.assertEqual(result["status"], "not_available")
        self.assertFalse(result["evidence_current"])
        self.assertFalse(result["production_changed"])

    def test_report_endpoint_is_read_only(self):
        expected = {"status": "historical_research", "production_changed": False}
        with patch("ml.evaluation.quant_report.read_report", return_value=expected), patch("api.planner.query") as query:
            self.assertEqual(equations(), expected)
        query.assert_not_called()


if __name__ == "__main__":
    unittest.main()
