"""Prospective captures cannot be rewritten, backdated by CLI, or valued early."""

from datetime import date, timedelta
import json
from pathlib import Path
import sys
import tempfile
import unittest
import subprocess
import os

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ml.evaluation.tournament_forward import capture, assess, freshness, momentum_return


class TournamentForwardTests(unittest.TestCase):
    def test_stale_or_future_signals_do_not_start_cohorts(self):
        self.assertFalse(freshness("2025-01-01", "2025-01-10"))
        self.assertFalse(freshness("2025-01-11", "2025-01-10"))
        with tempfile.TemporaryDirectory() as directory:
            result = capture(directory, "2025-01-10", "2025-01-01", "abc", {}, {})
            self.assertEqual(result["status"], "waiting_for_fresh_data")
            self.assertFalse(list(Path(directory).glob("*.json")))

    def test_daily_retry_preserves_original_predictions(self):
        with tempfile.TemporaryDirectory() as directory:
            result = capture(directory, "2025-01-10", "2025-01-09", "abcdef", {"candidate": [{"symbol": "A"}]}, {})
            original = Path(result["path"]).read_bytes()
            retry = capture(directory, "2025-01-10", "2025-01-09", "abcdef", {"candidate": [{"symbol": "B"}]}, {})
            self.assertEqual(retry["status"], "already_captured")
            self.assertEqual(Path(result["path"]).read_bytes(), original)

    def test_fill_is_after_actual_publication_not_old_signal_date(self):
        days = [(date(2025, 1, 1) + timedelta(days=i)).isoformat() for i in range(40)]
        from ml.evaluation.retail import Policy
        record = {"publication_date": days[10], "signal_date": days[6], "policy": Policy().__dict__, "baskets": {"candidate": [{"symbol": "A"}]}}
        bars = {("A", d): {"open": 100, "low": 99, "close": 100} for d in days}
        result = assess(record, bars, days, [])
        case = result["baskets"]["candidate"]["cases"][0]
        self.assertEqual(case["entry_date"], days[11])
        self.assertEqual(case["scheduled_exit_date"], days[32])
        pending = assess(record, bars, days[:20], [])
        self.assertIsNone(pending["baskets"]["candidate"]["net_return"])

    def test_momentum_uses_exact_calendar_lookback_and_no_future_price(self):
        days = [(date(2025, 1, 1) + timedelta(days=i)).isoformat() for i in range(70)]
        bars = {("A", days[0]): {"close": 100}, ("A", days[63]): {"close": 120},
                ("A", days[64]): {"close": 10000}}
        self.assertAlmostEqual(momentum_return("A", days[63], bars, days), .2)
        self.assertIsNone(momentum_return("A", days[62], bars, days))
        self.assertIsNone(momentum_return("B", days[63], bars, days))

    def test_failed_paper_stage_is_visible_without_changing_production(self):
        root = Path(__file__).resolve().parents[1]
        if not (root / "ml/.venv/bin/python").exists():
            self.skipTest("requires isolated ML environment")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "paper_candidate.json").write_text("{}")
            (path / "report.json").write_text("{}")
            result = subprocess.run(["bash", "scripts/tournament_paper.sh", str(path / "missing-input")],
                                    cwd=root, env={**os.environ, "TOURNAMENT_DIR": str(path)}, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            state = json.loads((path / "paper/status.json").read_text())
            self.assertEqual(state["status"], "error")
            self.assertEqual(state["failed_stage"], "capture_and_evaluate")
            self.assertFalse(state["production_changed"])


if __name__ == "__main__":
    unittest.main()
