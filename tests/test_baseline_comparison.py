"""Fairness, chronology and execution tests for the controlled comparison."""

from datetime import date, timedelta
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ml.evaluation.compare_baselines import candidates, select, allocate, cycle, compare_period
from ml.evaluation.retail import Policy


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.days = [(date(2025, 1, 1) + timedelta(days=i)).isoformat() for i in range(80)]
        self.policy = Policy(capital=100000, horizon=21, max_positions=1000)
        self.row = {"symbol": "A", "score": .1, "momentum": .2, "industry": "One",
                    "close": 100, "volume_avg_20d": 1000000, "volatility_20d": .02}
        self.bars = {("A", d): {"open": 100., "low": 99., "close": 100.} for d in self.days}

    def test_same_pool_filters_before_strategy_selection(self):
        predictions = [{"symbol": "A", "score": .1}, {"symbol": "B", "score": .9}]
        stats = {("A", self.days[0]): self.row,
                 ("B", self.days[0]): {**self.row, "symbol": "B", "volume_avg_20d": 1}}
        pool = candidates(predictions, stats, self.days[0], self.policy, [])
        self.assertEqual([r["symbol"] for r in pool], ["A"])
        for name in ("model", "momentum", "equal_weight"):
            self.assertEqual(select(pool, name), pool)

    def test_momentum_and_model_use_different_signals_same_members(self):
        pool = [{**self.row, "symbol": f"S{i:02}", "score": i, "momentum": -i} for i in range(25)]
        self.assertEqual(select(pool, "model")[0]["symbol"], "S24")
        self.assertEqual(select(pool, "momentum")[0]["symbol"], "S00")
        self.assertEqual(len(select(pool, "equal_weight")), 25)
        self.assertEqual(len(select(pool, "model")), 20)

    def test_identical_baskets_have_identical_costs_and_results(self):
        signals = {d: [{"symbol": "A", "score": .1}] for d in self.days}
        stats = {("A", d): self.row for d in self.days}
        result = compare_period(signals, stats, self.bars, self.days, self.policy, [], self.days[0], self.days[-1])
        returns = [r["net_return"] for r in result["strategies"].values()]
        self.assertEqual(returns, [returns[0]] * 3)
        self.assertLess(returns[0], 0)  # A flat stock still incurs both-side costs.
        self.assertTrue(all(p["mean_cycle_difference"] == 0 for p in result["paired_comparisons"].values()))
        diagnostic = result["selection_diagnostic"]["strategies"]
        self.assertEqual(len({r["net_return"] for r in diagnostic.values()}), 1)
        expected_cycle = (.999 * .999) / (1.001 * 1.001) - 1
        self.assertAlmostEqual(diagnostic["model"]["cycles"][0]["net_return"], expected_cycle)
        cycles = result["strategies"]["model"]["cycles"]
        for previous, current in zip(cycles, cycles[1:]):
            self.assertLess(previous["scheduled_exit_date"], current["entry_date"])
        for c in cycles:
            self.assertLess(c["signal_date"], c["entry_date"])

    def test_gap_stop_uses_open_and_does_not_reenter(self):
        self.bars[("A", self.days[2])] = {"open": 80, "low": 75, "close": 85}
        result = cycle([self.row], self.bars, self.days, 1, self.policy, 100000, [])
        self.assertEqual(result["status"], "complete")
        self.assertEqual(len(result["trades"]), 1)
        trade = result["trades"][0]
        self.assertAlmostEqual(trade["exit_price"], 80 * .999)
        self.assertEqual(trade["exit_reason"], "stop")
        self.assertEqual(result["curve"][-1]["positions"], 0)

    def test_missing_selected_entry_never_substitutes_another_stock(self):
        positions, cash, blocked = allocate([self.row], {}, self.days[1], 100000, self.policy)
        self.assertFalse(positions)
        self.assertEqual(cash, 100000)
        self.assertIsNotNone(blocked)

    def test_unvalued_future_event_is_not_filtered_with_hindsight(self):
        event = {"id": "event", "symbols": ["A"], "known_at": self.days[5],
                 "impact_start": self.days[8], "impact_end": self.days[9]}
        pool = candidates([{"symbol": "A", "score": .1}], {("A", self.days[0]): self.row},
                          self.days[0], self.policy, [event])
        self.assertEqual(len(pool), 1)
        result = cycle(pool, self.bars, self.days, 1, self.policy, 100000, [event])
        self.assertEqual(result["status"], "incomplete")
        self.assertIsNone(result["net_return"])

    def test_shadow_unknown_outcomes_remain_incomplete(self):
        event = {"id": "event", "symbols": ["A"], "known_at": self.days[5],
                 "impact_start": self.days[8], "impact_end": self.days[9]}
        signals = {d: [{"symbol": "A", "score": .1}] for d in self.days}
        stats = {("A", d): self.row for d in self.days}
        result = compare_period(signals, stats, self.bars, self.days, self.policy, [event], self.days[0], self.days[-1])
        shadow = result["selection_diagnostic"]["strategies"]["equal_weight"]
        self.assertEqual(shadow["status"], "incomplete")
        self.assertIsNone(shadow["net_return"])
        self.assertEqual(shadow["cycles"][0]["missing"], {"corporate_action": 1})

    def test_equal_targets_preserve_cash_and_sector_limits(self):
        rows = [{**self.row, "symbol": str(i)} for i in range(100)]
        bars = {(r["symbol"], self.days[0]): {"close": 100} for r in rows}
        positions, cash, blocked = allocate(rows, bars, self.days[0], 100000, self.policy)
        self.assertIsNone(blocked)
        exposure = sum(p["quantity"] * p["entry"] for p in positions.values())
        self.assertLessEqual(exposure, 30000)
        self.assertGreaterEqual(cash, 0)
        self.assertEqual(len({p["quantity"] for p in positions.values()}), 1)


if __name__ == "__main__":
    unittest.main()
