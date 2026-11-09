"""Historical report freshness guards require no market services."""

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ml.evaluation.baseline_report import read_report
from ml.evaluation.corporate_actions import fingerprint
from api.planner import baselines


class BaselineReportTests(unittest.TestCase):
    def test_missing_report_has_explicit_empty_state(self):
        with tempfile.TemporaryDirectory() as directory:
            report = read_report(Path(directory) / "absent.json")
        self.assertEqual(report["status"], "not_available")
        self.assertFalse(report["evidence_current"])

    def test_source_or_prediction_changes_mark_results_stale(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "ml/evaluation/compare_baselines.py"
            universe = root / "poller/universe/universe_cache.json"
            research = root / "research"
            source.parent.mkdir(parents=True)
            universe.parent.mkdir(parents=True)
            research.mkdir()
            source.write_text("original algorithm")
            universe.write_text("original universe")
            prediction = research / "rank21_fold1.csv"
            prediction.write_text("original predictions")
            config_path = research / "config.json"
            config_path.write_text("{}")
            sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
            config = {"events_digest": fingerprint([]), "source_hashes": {source.name: sha(source)},
                      "universe_sha256": sha(universe), "research_directory": str(research),
                      "research_config_sha256": sha(config_path), "prediction_files": {prediction.name: sha(prediction)}}
            path = root / "report.json"
            path.write_text(json.dumps({"status": "provisional", "config": config, "results": {}}))
            with patch("ml.evaluation.baseline_report.ROOT", root), patch("ml.evaluation.baseline_report.load_events", return_value=[]):
                self.assertTrue(read_report(path)["evidence_current"])
                prediction.write_text("changed predictions")
                report = read_report(path)
                self.assertEqual(report["status"], "stale_evidence")
                self.assertIn("Prediction evidence changed", report["stale_reasons"][0])
                source.write_text("changed algorithm")
                self.assertTrue(any("Research source changed" in r for r in read_report(path)["stale_reasons"]))

    def test_endpoint_only_reads_evidence(self):
        expected = {"status": "provisional", "results": {}}
        with patch("ml.evaluation.baseline_report.read_report", return_value=expected), patch("api.planner.query") as query:
            self.assertEqual(baselines(), expected)
        query.assert_not_called()


if __name__ == "__main__":
    unittest.main()
