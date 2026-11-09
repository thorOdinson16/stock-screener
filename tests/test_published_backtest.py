"""Non-overlapping capital accounting for the historical replay."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ml.evaluation.backtest_published import portfolio_statistics


def cohort(entry, exit_date, net, status="complete"):
    return {"entry_date": entry, "exit_date": exit_date, "published_at": entry,
            "status": status, "net_return": net, "universe_return": 0., "index_return": .01}


class PortfolioTests(unittest.TestCase):
    def test_initial_loss_counts_in_drawdown(self):
        result = portfolio_statistics([cohort("2026-01-01", "2026-01-08", -.1),
                                       cohort("2026-01-08", "2026-01-15", .2)])
        self.assertAlmostEqual(result["total_return"], .08)
        self.assertAlmostEqual(result["max_drawdown"], -.1)
        self.assertEqual(len(result["curve"]), 3)

    def test_overlapping_winner_is_not_added(self):
        result = portfolio_statistics([cohort("2026-01-01", "2026-01-08", -.1),
                                       cohort("2026-01-02", "2026-01-09", 1.),
                                       cohort("2026-01-08", "2026-01-15", 0.)])
        self.assertEqual(result["scheduled"], 2)
        self.assertAlmostEqual(result["total_return"], -.1)

    def test_incomplete_window_is_not_silently_skipped(self):
        result = portfolio_statistics([cohort("2026-01-01", "2026-01-08", None, "incomplete"),
                                       cohort("2026-01-02", "2026-01-09", 1.),
                                       cohort("2026-01-08", "2026-01-15", .2)])
        self.assertEqual(result["scheduled"], 2)
        self.assertEqual(result["incomplete"], 1)
        self.assertIsNone(result["total_return"])
        self.assertEqual(result["curve"], [])


if __name__ == "__main__":
    unittest.main()
