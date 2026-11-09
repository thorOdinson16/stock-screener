"""Prospective cohort accounting; run with python3 -m unittest discover -s tests -p test_forward_tracking.py."""

from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ml.evaluation.forward_tracking import build_report, capture, evaluate, read_report, summaries


class ForwardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.now = datetime(2026, 1, 2, 16, tzinfo=timezone.utc)
        self.rows = [
            {"symbol": "A", "rank_5d": 1, "score_5d": .9, "rank_21d": 2, "score_21d": .6},
            {"symbol": "B", "rank_5d": 2, "score_5d": .8, "rank_21d": 1, "score_21d": .7},
            {"symbol": "C", "rank_5d": 30, "score_5d": .1, "rank_21d": None, "score_21d": None},
        ]
        self.published = self.now.isoformat()
        capture(self.root, self.published, self.rows, {"version": "original"}, now=self.now)
        self.snapshot = json.loads(next((self.root / "predictions").glob("*.json")).read_text())
        self.sessions = []
        day = date(2026, 1, 5)
        while len(self.sessions) < 25:
            if day.weekday() < 5:
                self.sessions.append(day.isoformat())
            day += timedelta(days=1)
        self.prices = {(s, d): 100. for s in ["A", "B", "C", "^NSEI"] for d in self.sessions}
        self.prices[("A", self.sessions[5])] = 110.
        self.prices[("B", self.sessions[5])] = 90.
        self.prices[("C", self.sessions[5])] = 130.
        self.prices[("^NSEI", self.sessions[5])] = 105.

    def tearDown(self):
        self.temp.cleanup()

    def test_capture_is_immutable_across_retry_and_retrain(self):
        capture(self.root, self.published, self.rows[::-1], {"version": "new"}, now=self.now)
        files = list((self.root / "predictions").glob("*.json"))
        self.assertEqual(len(files), 1)
        self.assertEqual(json.loads(files[0].read_text()), self.snapshot)
        self.assertEqual([p["symbol"] for p in self.snapshot["picks"]["5"]], ["A", "B"])
        self.assertEqual([p["symbol"] for p in self.snapshot["picks"]["21"]], ["B", "A"])

    def test_old_predictions_cannot_be_backfilled(self):
        with self.assertRaises(ValueError):
            capture(self.root, "2025-12-01T00:00:00Z", self.rows, {}, now=self.now)

    def test_no_entry_before_publication_or_on_weekend(self):
        result = evaluate(self.snapshot, 5, self.prices, self.sessions[:1])
        self.assertEqual(result["entry_date"], "2026-01-05")
        self.assertEqual(result["status"], "pending")
        self.assertEqual(len(result["picks"]), 2)
        self.assertIsNone(result["net_return"])

    def test_ist_date_controls_entry(self):
        published = "2026-01-05T20:00:00Z"  # January 6 in India
        capture(self.root, published, self.rows, {}, now=datetime.fromisoformat(published.replace("Z", "+00:00")))
        snapshot = next(json.loads(p.read_text()) for p in (self.root / "predictions").glob("*.json") if json.loads(p.read_text())["published_at"] == published)
        result = evaluate(snapshot, 5, self.prices, self.sessions)
        self.assertEqual(result["entry_date"], "2026-01-07")

    def test_equal_weight_cost_benchmarks_and_hits(self):
        result = evaluate(self.snapshot, 5, self.prices, self.sessions)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["exit_date"], self.sessions[5])
        self.assertAlmostEqual(result["gross_return"], 0)
        self.assertAlmostEqual(result["net_return"], .999 / 1.001 - 1)
        self.assertAlmostEqual(result["universe_return"], .1)
        self.assertAlmostEqual(result["index_return"], .05)
        self.assertEqual(result["hit_rate"], 0)  # A ties the universe; ties are not wins.
        self.assertAlmostEqual(result["worst_pick_return"], -.1)
        self.assertAlmostEqual(result["excess_return"], result["net_return"] - .1)

    def test_hit_rate_and_twenty_one_session_exit(self):
        self.prices[("A", self.sessions[21])] = 120.
        self.prices[("B", self.sessions[21])] = 100.
        self.prices[("C", self.sessions[21])] = 80.
        result = evaluate(self.snapshot, 21, self.prices, self.sessions)
        self.assertEqual(result["exit_date"], self.sessions[21])
        self.assertAlmostEqual(result["gross_return"], .1)
        self.assertEqual(result["hit_rate"], .5)

    def test_missing_picks_are_not_dropped_or_date_shifted(self):
        del self.prices[("B", self.sessions[5])]
        result = evaluate(self.snapshot, 5, self.prices, self.sessions)
        self.assertEqual(result["status"], "incomplete")
        self.assertIsNone(result["net_return"])
        self.assertIsNone(result["picks"][1]["gross_return"])
        self.assertEqual(result["exit_date"], self.sessions[5])

    def test_missing_universe_member_blocks_benchmark(self):
        del self.prices[("C", self.sessions[5])]
        result = evaluate(self.snapshot, 5, self.prices, self.sessions)
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["universe_coverage"], 2)
        self.assertIsNone(result["universe_return"])

    def test_today_is_excluded_and_complete_results_are_frozen(self):
        report = build_report(self.root, self.prices, self.sessions, self.sessions[5])
        five = next(r for r in report["cohorts"] if r["horizon"] == 5)
        self.assertEqual(five["status"], "pending")
        report = build_report(self.root, self.prices, self.sessions, self.sessions[6])
        five = next(r for r in report["cohorts"] if r["horizon"] == 5)
        self.assertEqual(five["status"], "complete")
        frozen = five.copy()
        self.prices[("A", self.sessions[5])] = 999.
        report = build_report(self.root, self.prices, self.sessions, self.sessions[7])
        self.assertEqual(next(r for r in report["cohorts"] if r["horizon"] == 5), frozen)

    def test_summary_does_not_count_overlaps_as_independent(self):
        row = evaluate(self.snapshot, 5, self.prices, self.sessions)
        stats = summaries([row, {**row, "id": "second"}])["5"]
        self.assertEqual(stats["complete"], 2)
        self.assertEqual(stats["nonoverlapping_count"], 1)
        self.assertEqual(stats["months"][0]["cohorts"], 2)

    def test_empty_report_is_valid_json_with_no_performance_claim(self):
        report = read_report(self.root / "empty")
        self.assertEqual(report["cohorts"], [])
        self.assertIsNone(report["summaries"]["21"]["mean_net_return"])
        json.dumps(report, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
