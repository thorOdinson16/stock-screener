"""Diagnostic accounting and missing-outcome handling."""
from pathlib import Path
import sys
import unittest
import numpy as np
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ml.evaluation.diagnose_predictions import ranking_metrics, trade_attribution


class RankingTests(unittest.TestCase):
    def frame(self):
        return pd.DataFrame({'symbol': [str(i) for i in range(25)], 'trade_date': '2020-01-01',
                             'score': np.arange(25), 'net_return': np.arange(25) / 100,
                             'legacy_target': -np.arange(25) / 100})

    def test_target_disagreement_and_equal_date_weight(self):
        f = self.frame()
        g = self.frame().iloc[:20].copy()
        g['trade_date'] = '2020-01-02'
        g['net_return'] *= -1
        r = ranking_metrics(pd.concat([f, g], ignore_index=True))
        self.assertAlmostEqual(r['mean_daily_rank_ic'], 0)
        self.assertAlmostEqual(r['mean_legacy_rank_ic'], -1)
        self.assertEqual(r['ic_dates'], 2)

    def test_missing_top_outcome_never_gets_replaced(self):
        f = self.frame()
        f.loc[24, 'net_return'] = np.nan
        r = ranking_metrics(f)
        self.assertEqual(r['known_execution_rows'], 24)
        self.assertEqual(r['complete_top20_dates'], 0)
        self.assertEqual(r['daily'][0]['selected_unknown'], 1)
        self.assertIsNone(r['mean_top20_net'])

    def test_too_small_cross_section_has_no_ic(self):
        self.assertIsNone(ranking_metrics(self.frame().head(4), 5)['mean_daily_rank_ic'])


class AccountingTests(unittest.TestCase):
    def simulation(self):
        quantity, entry, exit_price, fee, slip = 10, 100, 110, .001, .001
        cost = quantity * entry * (1 + slip) * (1 + fee)
        proceeds = quantity * exit_price * (1 - slip) * (1 - fee)
        return {'policy': {'capital': 10000, 'slippage_bps': 10}, 'status': 'complete', 'open_positions': {},
                'trades': [{'symbol': 'A', 'industry': 'sector', 'exit_date': '2020-01-30',
                            'entry_price': entry * (1 + slip), 'exit_price': exit_price * (1 - slip),
                            'quantity': quantity, 'cost': cost, 'proceeds': proceeds,
                            'pnl': proceeds - cost, 'exit_reason': 'time'}]}

    def test_exact_cost_bridge_on_same_trades(self):
        r = trade_attribution(self.simulation())
        self.assertAlmostEqual(r['gross_pnl'], 100)
        self.assertAlmostEqual(r['gross_pnl'] - r['cost_drag'], r['net_pnl'])
        self.assertGreater(r['cost_drag'], 0)
        self.assertAlmostEqual(sum(v['net_pnl'] for v in r['by_sector'].values()), r['net_pnl'])
        self.assertAlmostEqual(sum(v['net_pnl'] for v in r['by_exit_quarter'].values()), r['net_pnl'])

    def test_incomplete_book_remains_prefix(self):
        s = self.simulation()
        s['status'] = 'incomplete'
        self.assertEqual(trade_attribution(s)['status'], 'closed_trade_prefix')


if __name__ == '__main__':
    unittest.main()
