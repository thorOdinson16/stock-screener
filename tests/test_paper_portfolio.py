"""Prospective books value open trades without inventing matured returns."""

from datetime import date, timedelta
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ml.evaluation.paper_portfolio import evaluate_book
from ml.evaluation.retail import Policy


class PaperBookTests(unittest.TestCase):
    def setUp(self):
        self.days = [(date(2025, 1, 1) + timedelta(days=i)).isoformat() for i in range(40)]
        self.record = {"publication_date": self.days[0], "policy": Policy().__dict__}
        self.rows = [{"symbol": "A", "industry": "Sector", "volume_avg_20d": 1000000, "volatility_20d": .02}]
        self.bars = {("A", day): {"open": 100., "low": 99., "close": 100.} for day in self.days}

    def test_no_trade_before_a_session_after_publication(self):
        result = evaluate_book(self.record, self.rows, self.bars, self.days[:1], [])
        self.assertEqual(result["status"], "pending_entry")
        self.assertIsNone(result["net_return"])

    def test_open_book_marks_costs_without_claiming_matured_return(self):
        result = evaluate_book(self.record, self.rows, self.bars, self.days[:3], [])
        self.assertEqual(result["status"], "open")
        self.assertIsNone(result["net_return"])
        self.assertLess(result["mark_to_market_return"], 0)
        self.assertTrue(result["positions"])
        self.assertEqual(result["entry_date"], self.days[1])

    def test_gap_stop_and_whole_share_cash_accounting(self):
        self.bars[("A", self.days[2])] = {"open": 80., "low": 75., "close": 85.}
        result = evaluate_book(self.record, self.rows, self.bars, self.days, [])
        self.assertEqual(result["status"], "complete")
        self.assertLess(result["net_return"], 0)
        self.assertAlmostEqual(result["trades"][0]["exit_price"], 80 * .999)
        self.assertFalse(result["positions"])
        self.assertIn("A", result["entry_receipt"]["positions"])
        self.assertTrue(all(p["cash"] >= 0 for p in result["curve"]))

    def test_unvalued_action_is_incomplete_not_a_synthetic_loss(self):
        event = {"symbols": ["A"], "impact_start": self.days[3], "impact_end": self.days[4]}
        result = evaluate_book(self.record, self.rows, self.bars, self.days, [event])
        self.assertEqual(result["status"], "incomplete")
        self.assertIsNone(result["net_return"])

    def test_frozen_fills_are_not_resized_after_price_basis_revision(self):
        initial = evaluate_book(self.record, self.rows, self.bars, self.days[:3], [])
        receipt = initial["entry_receipt"]
        repeated = evaluate_book(self.record, self.rows, self.bars, self.days[:5], [], receipt)
        self.assertEqual(repeated["allocation"], initial["allocation"])
        self.bars[("A", self.days[1])]["close"] = 50.
        revised = evaluate_book(self.record, self.rows, self.bars, self.days, [], receipt)
        self.assertEqual(revised["status"], "incomplete")
        self.assertIsNone(revised["net_return"])
        self.assertIn("basis revised", revised["reason"])


if __name__ == "__main__":
    unittest.main()
