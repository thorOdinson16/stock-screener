"""Tournament API reads evidence without numerical dependencies or market calls."""

import json
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ml.evaluation.tournament_report import read_report
from api.planner import tournament
from ml.evaluation.corporate_actions import fingerprint, load_events


class TournamentReportTests(unittest.TestCase):
    def test_running_state_does_not_pretend_to_have_completed_results(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            self.assertEqual(read_report(path)["status"], "not_available")
            (path / "progress.json").write_text(json.dumps({"stage": "validation_fit", "candidate": "price_only_execution_linear"}))
            report = read_report(path)
            self.assertEqual(report["status"], "running")
            self.assertEqual(report["results"], {})
            self.assertFalse(report["production_changed"])

    def test_endpoint_reads_only_local_evidence(self):
        expected = {"status": "provisional", "results": {}}
        with patch("ml.evaluation.tournament_report.read_report", return_value=expected), patch("api.planner.query") as query:
            self.assertEqual(tournament(), expected)
        query.assert_not_called()

    def test_failed_later_assessment_and_changed_data_are_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs, reference = root / "inputs", root / "reference"
            inputs.mkdir()
            reference.mkdir()
            data = inputs / "data.parquet"
            data.write_bytes(b"original research data")
            digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
            manifest = {"events_digest": fingerprint(load_events()), "source_hashes": {}, "files": {data.name: digest(data)}}
            manifest_path = inputs / "manifest.json"
            manifest_path.write_text(json.dumps(manifest))
            protocol = {"source_hashes": {}, "input_directory": str(inputs), "input_manifest": digest(manifest_path),
                        "reference_directory": str(reference), "reference_files": {}}
            (root / "protocol.json").write_text(json.dumps(protocol))
            metric = lambda r: {"status": "complete", "net_return": r, "max_drawdown": -.1, "closed_trades": 100}
            period = {"retail": {s: metric(-.02) for s in ("base", "double_costs", "entry_delayed")},
                      "matched_momentum_retail": {s: metric(.3) for s in ("base", "double_costs", "entry_delayed")},
                      "selection_diagnostic": {"strategies": {"model": {"cycles": [], "net_return": -.01}}}}
            (root / "report.json").write_text(json.dumps({"status": "provisional", "protocol": protocol,
                   "selected_on_validation": "tcn", "results": {"tcn": {"assessment": period}}}))
            current = read_report(root)
            self.assertTrue(current["evidence_current"])
            self.assertFalse(current["paper_candidate_assessment"]["supported"])
            self.assertEqual(len(current["paper_candidate_assessment"]["reasons"]), 3)
            self.assertNotIn("cycles", current["results"]["tcn"]["assessment"]["selection_diagnostic"]["strategies"]["model"])
            data.write_bytes(b"changed data")
            stale = read_report(root)
            self.assertEqual(stale["status"], "stale_evidence")
            self.assertIn("Exported data changed: data.parquet", stale["stale_reasons"])


if __name__ == "__main__":
    unittest.main()
