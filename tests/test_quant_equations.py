"""Equation identities, causal windows and honest comparison gates."""

from pathlib import Path
import sys
import unittest
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ml.evaluation.quant_equations import symbol_equations, adjusted_pair_check


class EquationTests(unittest.TestCase):
    def setUp(self):
        rng = np.random.default_rng(42)
        self.market = pd.Series(100 * np.exp(np.cumsum(rng.normal(.0004, .01, 340))))
        self.stock = pd.Series(100 * np.exp(np.cumsum(.3 * np.log(self.market).diff().fillna(0).to_numpy() + rng.normal(.0005, .015, 340))))

    def test_future_prices_cannot_change_past_scores(self):
        original = symbol_equations(self.stock, self.market)
        changed = self.stock.copy()
        changed.iloc[310:] *= 2
        later_market = self.market.copy()
        later_market.iloc[310:] *= 3
        pd.testing.assert_frame_equal(original.iloc[:310], symbol_equations(changed, later_market).iloc[:310])

    def test_skipped_month_identity(self):
        result = symbol_equations(self.stock, self.market)
        self.assertAlmostEqual(result.momentum_12_minus_1.iloc[-1], np.log(self.stock.iloc[-22] / self.stock.iloc[-253]))
        changed = self.stock.copy()
        changed.iloc[-20:] *= 2
        self.assertAlmostEqual(symbol_equations(changed, self.market).momentum_12_minus_1.iloc[-1], result.momentum_12_minus_1.iloc[-1])

    def test_market_residual_matches_explicit_trailing_ols(self):
        result = symbol_equations(self.stock, self.market)
        r = np.log(self.stock).diff().iloc[-252:].to_numpy()
        m = np.log(self.market).diff().iloc[-252:].to_numpy()
        beta = np.cov(r, m, ddof=1)[0, 1] / np.var(m, ddof=1)
        alpha = r.mean() - beta * m.mean()
        residual = r - alpha - beta * m
        expected = residual[-63:].sum() / (np.std(residual, ddof=1) * np.sqrt(63))
        self.assertAlmostEqual(result.market_residual_momentum.iloc[-1], expected, places=9)

    def test_breakout_uses_previous_high_and_gaps_stay_unavailable(self):
        stock = self.stock.copy()
        stock.iloc[-1] = stock.iloc[-64:-1].max() * 1.1
        result = symbol_equations(stock, self.market)
        self.assertGreater(result.breakout_63.iloc[-1], 0)
        stock.iloc[-100] = np.nan
        self.assertTrue(symbol_equations(stock, self.market).iloc[-1].isna().all())

    def test_constant_prices_do_not_create_infinite_conviction(self):
        result = symbol_equations(pd.Series([100.] * 340), self.market)
        self.assertTrue(pd.isna(result.risk_adjusted_momentum.iloc[-1]))
        self.assertFalse(np.isinf(result.to_numpy()).any())

    def test_incomplete_comparison_cannot_pass(self):
        metric = {"status": "incomplete", "net_return": None}
        result = {"retail": {n: metric for n in ("base", "double_costs", "entry_delayed")},
                  "matched_momentum_retail": {n: metric for n in ("base", "double_costs", "entry_delayed")},
                  "selection_diagnostic": {"strategies": {"model": {"status": "incomplete", "cycles": []}, "momentum": {"status": "incomplete", "cycles": []}}}}
        check = adjusted_pair_check(result)
        self.assertFalse(check["supported"])
        self.assertFalse(check["production_ready"])


if __name__ == "__main__":
    unittest.main()
