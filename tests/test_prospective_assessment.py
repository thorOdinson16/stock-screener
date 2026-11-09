"""Future validation cannot substitute overlaps or omit unavailable losers."""

from datetime import date, timedelta
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ml.evaluation.prospective_assessment import summarize


class ProspectiveTests(unittest.TestCase):
    def setUp(self):
        self.days = [(date(2025, 1, 1) + timedelta(days=i)).isoformat() for i in range(250)]

    def cohort(self, index, missing=False):
        record = {"publication_date": self.days[index], "policy": {"horizon": 21}}
        book = lambda value: {"status": "complete", "net_return": value}
        books = {"candidate": book(.03), "momentum": book(.01), "current_model": book(.01), "tcn_control": book(.01)}
        if missing:
            books["candidate"] = {"status": "incomplete", "net_return": None}
        return record, {"retail_books": books}

    def test_first_signal_has_no_invented_return(self):
        result = summarize([self.cohort(0)], self.days[:10])
        self.assertEqual(result["status"], "awaiting_mature_cohorts")
        self.assertEqual(result["matured_cohorts"], 0)
        self.assertFalse(result["production_ready"])

    def test_overlapping_cohorts_are_not_independent_trials(self):
        result = summarize([self.cohort(i) for i in range(20)], self.days)
        self.assertEqual(result["scheduled_independent_cohorts"], 1)
        self.assertFalse(result["comparisons"]["momentum"]["supported"])

    def test_missing_first_cycle_does_not_shift_the_schedule(self):
        result = summarize([self.cohort(0, True), self.cohort(1), self.cohort(23)], self.days)
        self.assertEqual(result["scheduled_independent_cohorts"], 2)
        self.assertEqual(result["comparisons"]["momentum"]["completed_independent_pairs"], 1)

    def test_strong_sufficient_fresh_pairs_can_pass_without_promotion(self):
        result = summarize([self.cohort(i * 22) for i in range(9)], self.days)
        self.assertTrue(all(c["supported"] for c in result["comparisons"].values()))
        self.assertFalse(result["production_ready"])


if __name__ == "__main__":
    unittest.main()
