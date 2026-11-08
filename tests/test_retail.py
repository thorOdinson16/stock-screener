from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ml.evaluation.retail import Policy, plan, simulate


def candidate(symbol="A", industry="Sector", rank=1):
    return {"symbol": symbol, "industry": industry, "rank": rank, "score": .2,
            "close": 100., "volume_avg_20d": 1000000., "volatility_20d": .02,
            "trade_date": "2026-01-01"}


class RetailTests(unittest.TestCase):
    def test_whole_shares_and_cash_risk_caps(self):
        policy = Policy()
        result = plan([candidate()], policy, "2026-01-02", events=[])
        pick = result["candidates"][0]
        self.assertEqual(pick["quantity"], int(pick["quantity"]))
        self.assertLessEqual(pick["planned_risk"], policy.capital * policy.risk_pct / 100)
        self.assertGreaterEqual(result["cash_remaining"], 0)
        self.assertLessEqual(pick["weight_pct"], policy.max_position_pct)

    def test_sector_exposure_and_existing_holdings(self):
        policy = Policy(max_sector_pct=10)
        holdings = [{"symbol": "OLD", "industry": "Sector", "quantity": 90, "price": 100.}]
        result = plan([candidate()], policy, "2026-01-02", holdings, events=[])
        self.assertLessEqual(sum(c["quantity"] * c["assumed_entry"] for c in result["candidates"]) + 9000, 10000)

    def test_stale_data_and_missing_liquidity_do_not_force_picks(self):
        result = plan([candidate()], Policy(), "2026-02-01", events=[])
        self.assertEqual(result["status"], "no_suitable_setup")
        row = candidate()
        row["volume_avg_20d"] = None
        self.assertFalse(plan([row], Policy(), "2026-01-01", events=[])["candidates"])

    def test_no_probability_is_invented(self):
        result = plan([candidate()], Policy(), "2026-01-01", events=[])
        self.assertIsNone(result["probability"])
        self.assertIsNone(result["expected_return"])

    def test_cash_too_small_for_one_share(self):
        result = plan([candidate()], Policy(capital=10), "2026-01-01", events=[])
        self.assertFalse(result["candidates"])
        self.assertEqual(result["cash_remaining"], 10)

    def test_simulator_enters_next_session_and_charges_costs(self):
        days = ["2026-01-01", "2026-01-02", "2026-01-05", "2026-01-06"]
        bars = {("A", day): {"open": 100., "low": 100., "close": 100.} for day in days}
        result = simulate({days[0]: [candidate()]}, bars, days, Policy(horizon=1), events=[])
        self.assertEqual(result["trades"][0]["entry_date"], days[1])
        self.assertEqual(result["trades"][0]["exit_date"], days[2])
        self.assertLess(result["net_return"], 0)
        self.assertTrue(all(p["cash"] >= 0 for p in result["curve"]))

    def test_stop_gap_fills_below_planned_stop(self):
        days = ["2026-01-01", "2026-01-02", "2026-01-05", "2026-01-06"]
        bars = {("A", day): {"open": 100., "low": 100., "close": 100.} for day in days}
        bars[("A", days[2])] = {"open": 80., "low": 79., "close": 85.}
        result = simulate({days[0]: [candidate()]}, bars, days, Policy(horizon=2), events=[])
        self.assertEqual(result["trades"][0]["exit_reason"], "stop")
        self.assertLess(result["trades"][0]["exit_price"], 80)

    def test_unvalued_action_does_not_become_a_loss_or_disappear(self):
        days = ["2026-01-01", "2026-01-02", "2026-01-05", "2026-01-06"]
        bars = {("A", day): {"open": 100., "low": 100., "close": 100.} for day in days}
        events = [{"id": "splitoff", "symbols": ["A"], "impact_start": days[2], "impact_end": days[2], "known_at": days[2]}]
        result = simulate({days[0]: [candidate()]}, bars, days, Policy(horizon=2), events=events)
        self.assertEqual(result["status"], "incomplete")
        self.assertIsNone(result["net_return"])
        self.assertEqual(result["blocked"]["symbol"], "A")

    def test_missing_ohlc_does_not_silently_disable_stop(self):
        days = ["2026-01-01", "2026-01-02", "2026-01-05", "2026-01-06"]
        bars = {("A", day): {"open": 100., "low": 100., "close": 100.} for day in days}
        bars[("A", days[2])]["low"] = None
        result = simulate({days[0]: [candidate()]}, bars, days, Policy(horizon=2), events=[])
        self.assertEqual(result["status"], "incomplete")
        self.assertIsNone(result["net_return"])

    def test_lookahead_announcement_does_not_block_earlier_entry(self):
        events = [{"symbols": ["A"], "impact_start": "2026-06-01", "impact_end": "2026-06-01", "known_at": "2026-05-01"}]
        self.assertTrue(plan([candidate()], Policy(), "2026-01-01", events=events)["candidates"])

    def test_invalid_settings_are_rejected(self):
        for policy in [Policy(capital=-1), Policy(stop_pct=0), Policy(fee_bps=float("nan")), Policy(max_positions=1.5)]:
            with self.assertRaises(ValueError):
                policy.validate()


if __name__ == "__main__":
    unittest.main()
