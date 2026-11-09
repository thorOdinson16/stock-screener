"""The completion report needs no model-training libraries in the API env."""

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ml.evaluation.completion_report import read_report
from api.planner import completion


class CompletionApiTests(unittest.TestCase):
    def test_missing_run_has_no_completed_or_live_claim(self):
        with tempfile.TemporaryDirectory() as directory:
            result = read_report(directory)
        self.assertEqual(result["status"], "not_available")
        self.assertFalse(result["evidence_current"])
        self.assertFalse(result["production_changed"])

    def test_completion_endpoint_reads_evidence_without_market_queries(self):
        expected = {"status": "historical_research", "production_changed": False}
        with patch("ml.evaluation.completion_report.read_report", return_value=expected), patch("api.planner.query") as query:
            self.assertEqual(completion(), expected)
        query.assert_not_called()


if __name__ == "__main__":
    unittest.main()
