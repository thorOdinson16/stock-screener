import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "poller"))
from ml.evaluation.corporate_actions import adjusted_return, load_events, unadjusted_wealth
from incremental import adjustment_revision


class CorporateActionTests(unittest.TestCase):
    def test_known_demerger_is_not_a_price_loss(self):
        prices = {("VEDL.NS", "2026-04-29"): 700., ("VEDL.NS", "2026-05-04"): 280.}
        value, reasons = adjusted_return("VEDL.NS", "2026-04-29", "2026-05-04", prices, load_events())
        self.assertIsNone(value)
        self.assertIn("vedanta-demerger-2026", reasons)

    def test_provider_adjusted_prices_are_not_adjusted_twice(self):
        prices = {("A", "2026-01-01"): 100., ("A", "2026-01-10"): 110.}
        value, _ = adjusted_return("A", "2026-01-01", "2026-01-10", prices, [])
        self.assertAlmostEqual(value, .1)

    def test_verified_entitlement_preserves_wealth(self):
        prices = {("A", "2026-01-01"): 100., ("A", "2026-01-10"): 40., ("B", "2026-01-10"): 60.}
        actions = [{"symbol": "A", "date": "2026-01-05", "kind": "demerger", "verified": True,
                    "source": "official", "entitlements": [{"symbol": "B", "ratio": 1}]}]
        result = unadjusted_wealth("A", "2026-01-01", "2026-01-10", prices, actions)
        self.assertEqual(result["return"], 0)
        del prices[("B", "2026-01-10")]
        self.assertIsNone(unadjusted_wealth("A", "2026-01-01", "2026-01-10", prices, actions)["return"])

    def test_split_and_cash_dividend_use_current_share_count(self):
        actions = [{"symbol": "A", "date": "2026-01-05", "kind": "split", "verified": True, "source": "official", "ratio": 2},
                   {"symbol": "A", "date": "2026-01-06", "kind": "dividend", "verified": True, "source": "official", "cash_per_share": 5}]
        prices = {("A", "2026-01-01"): 100., ("A", "2026-01-10"): 55.}
        result = unadjusted_wealth("A", "2026-01-01", "2026-01-10", prices, actions)
        self.assertAlmostEqual(result["return"], .2)

    def test_unverified_terms_cannot_create_returns(self):
        result = unadjusted_wealth("A", "2026-01-01", "2026-01-10",
                                  {("A", "2026-01-01"): 100.},
                                  [{"symbol": "A", "date": "2026-01-05", "kind": "demerger"}])
        self.assertIsNone(result["return"])

    def test_overlap_revision_requires_multiple_historical_changes(self):
        reference = {"closes": {"2026-01-01": 100., "2026-01-02": 100., "2026-01-03": 100.}}
        rows = [{"trade_date": day, "close": 50.} for day in reference["closes"]]
        self.assertTrue(adjustment_revision(rows, reference))
        rows[0]["close"] = rows[1]["close"] = 100.
        self.assertFalse(adjustment_revision(rows, reference))


if __name__ == "__main__":
    unittest.main()
