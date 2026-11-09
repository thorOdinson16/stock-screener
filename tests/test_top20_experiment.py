"""Fine relevance, economic selection and real CPU fit checks."""
from pathlib import Path
import sys
import unittest
import numpy as np
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ml.evaluation.top20_experiment import fine_labels, fit_target, choose_config, economic_score, candidate_gate


class LabelTests(unittest.TestCase):
    def test_fine_labels_preserve_ties_and_distinguish_top_decile(self):
        f = pd.DataFrame({'trade_date': ['a'] * 100, 'net_return': np.arange(100, dtype=float)})
        f.loc[1, 'net_return'] = 0
        y = fine_labels(f)
        self.assertEqual(y[0], y[1])
        self.assertEqual(len(set(y[-10:])), 10)
        self.assertTrue(np.all(np.diff(y) >= 0))

    def test_ranks_are_date_local(self):
        f = pd.DataFrame({'trade_date': ['a','a','b','b'], 'net_return': [-1,0,100,200]})
        np.testing.assert_equal(fine_labels(f), [0,1,0,1])

    def test_real_fits_accept_more_than_31_labels(self):
        x = np.arange(120, dtype=float)
        f = pd.DataFrame({'symbol': list(map(str, range(120))), 'trade_date': np.repeat(['a','b'],60),
                          'feature': x, 'net_return': x / 1000})
        for arm in ('fine_rank', 'net_regression'):
            model = fit_target(f, ['feature'], arm, {'max_depth': 2, 'n_estimators': 2})
            self.assertTrue(np.isfinite(model.predict(f[['feature']].to_numpy(dtype=np.float32))).all())


class SelectionTests(unittest.TestCase):
    def result(self):
        cycles = lambda value: [{'signal_date': str(i), 'status': 'complete', 'net_return': value} for i in range(9)]
        return {'selection_diagnostic': {'strategies': {
            'model': {'status': 'complete', 'net_return': .2, 'cycles': cycles(.03)},
            'momentum': {'status': 'complete', 'net_return': .1, 'cycles': cycles(.01)}}},
            'retail': {s: {'status': 'complete', 'net_return': .1, 'closed_trades': 30, 'max_drawdown': -.1} for s in ('base','double_costs','entry_delayed')},
            'matched_momentum_retail': {s: {'status': 'complete', 'net_return': .05} for s in ('base','double_costs','entry_delayed')}}

    def test_ic_and_economic_rules_choose_different_configs(self):
        trials = [{'mean_daily_rank_ic': .2, 'mean_cycle_excess': -.01},
                  {'mean_daily_rank_ic': .1, 'mean_cycle_excess': .02}]
        self.assertEqual(choose_config(trials, 'ic'), 0)
        self.assertEqual(choose_config(trials, 'top20_net'), 1)
        self.assertEqual(choose_config([trials[1], trials[1]], 'top20_net'), 0)

    def test_incomplete_economics_cannot_win(self):
        r = self.result()
        r['selection_diagnostic']['strategies']['model']['status'] = 'incomplete'
        self.assertIsNone(economic_score(r))
        self.assertIsNone(choose_config([{'mean_cycle_excess': None}], 'top20_net'))

    def test_cycle_dates_must_pair(self):
        r = self.result()
        r['selection_diagnostic']['strategies']['model']['cycles'][0]['signal_date'] = 'other'
        with self.assertRaisesRegex(ValueError, 'Unpaired'):
            economic_score(r)

    def test_fold_consistency_and_stress_gate(self):
        r = self.result()
        folds = {str(i): i // 3 + 1 for i in range(9)}
        reasons, stability = candidate_gate(r, folds)
        self.assertEqual(reasons, [])
        self.assertEqual(len(stability), 3)
        r['retail']['double_costs']['net_return'] = -.01
        self.assertTrue(candidate_gate(r, folds)[0])

    def test_missing_cycle_cannot_be_silently_dropped(self):
        r = self.result()
        r['selection_diagnostic']['strategies']['momentum']['cycles'][0]['status'] = 'incomplete'
        self.assertIsNone(economic_score(r))
        folds = {str(i): i // 3 + 1 for i in range(9)}
        self.assertIn('Incomplete equal-notional comparison', candidate_gate(r, folds)[0])


if __name__ == '__main__':
    unittest.main()
