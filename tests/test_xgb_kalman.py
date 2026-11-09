"""Causal filtering, gap resets and real grouped XGBoost fitting."""
from pathlib import Path
import sys
import unittest
import numpy as np
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ml.evaluation.xgb_kalman import kalman_scores, fit_xgb, build_kalman, paired_check
from ml.evaluation.tournament_models import training_rows


class KalmanTests(unittest.TestCase):
    def test_future_changes_cannot_change_past_scores(self):
        rng = np.random.default_rng(12)
        prices = np.exp(4 + np.cumsum(rng.normal(.001, .01, 400)))
        original = kalman_scores(prices)
        changed = prices.copy()
        changed[310:] *= np.linspace(2, 10, 90)
        np.testing.assert_equal(original[:310], kalman_scores(changed)[:310])
        self.assertTrue(np.isfinite(original[252:]).all())
        self.assertTrue(np.isnan(original[:252]).all())

    def test_gap_and_event_require_full_new_warmup(self):
        prices = np.exp(4 + np.arange(700) * .001)
        prices[300] = np.nan
        resets = np.zeros(700, dtype=bool)
        resets[600] = True
        scores = kalman_scores(prices, resets)
        self.assertTrue(np.isnan(scores[300:553]).all())
        self.assertTrue(np.isfinite(scores[553:600]).all())
        self.assertTrue(np.isnan(scores[600:]).all())

    def test_trend_sign_and_price_scale_invariance(self):
        rising = np.exp(4 + np.arange(350) * .002)
        falling = np.exp(4 - np.arange(350) * .002)
        a = kalman_scores(rising)
        self.assertGreater(a[-1], 0)
        self.assertLess(kalman_scores(falling)[-1], 0)
        np.testing.assert_allclose(a[252:], kalman_scores(rising * 100)[252:], atol=1e-9)

    def test_zero_negative_and_infinite_prices_reset(self):
        for bad in (0, -1, np.inf):
            prices = np.exp(4 + np.arange(300) * .001)
            prices[260] = bad
            self.assertTrue(np.isnan(kalman_scores(prices)[260:]).all())

    def test_common_calendar_gap_cannot_be_compressed(self):
        days = [str(i) for i in range(300)]
        bars = {('A', d): {'close': 100 + i} for i, d in enumerate(days) if i != 270}
        frame = pd.DataFrame({'symbol': ['A'] * 300, 'trade_date': days})
        scored = build_kalman(frame, bars, days, [])
        self.assertEqual(set(scored.trade_date), set(days[252:270]))


class XGBTests(unittest.TestCase):
    def sample(self):
        rng = np.random.default_rng(12)
        x = rng.normal(size=120)
        return pd.DataFrame({'symbol': [str(i % 40) for i in range(120)],
                             'trade_date': np.repeat(['2020-01-01', '2020-01-02', '2020-01-03'], 40),
                             'feature': x, 'net_return': x / 10, 'excess_ret_21d': x / 12,
                             'scheduled_exit_date': '2020-02-01', 'legacy_maturity': '2020-02-01'})

    def test_real_ranker_accepts_group_weights_and_is_deterministic(self):
        frame = self.sample()
        params = {'max_depth': 2, 'n_estimators': 4}
        model = fit_xgb(frame.sample(frac=1, random_state=42), ['feature'], 'execution', params)
        scores = model.predict(frame[['feature']].to_numpy(dtype=np.float32))
        other = fit_xgb(frame, ['feature'], 'execution', params)
        np.testing.assert_equal(scores, other.predict(frame[['feature']].to_numpy(dtype=np.float32)))
        self.assertTrue(np.isfinite(scores).all())
        self.assertEqual(model.get_xgb_params()['objective'], 'rank:ndcg')

    def test_maturity_is_strict_even_for_early_exit(self):
        frame = self.sample()
        frame['actual_exit_date'] = '2020-01-04'
        self.assertTrue(training_rows(frame, 'execution', frame.trade_date.unique(), '2020-02-01').empty)
        self.assertEqual(len(training_rows(frame, 'execution', frame.trade_date.unique(), '2020-02-02')), 120)

    def test_nonfinite_features_are_rejected(self):
        frame = self.sample()
        frame.loc[0, 'feature'] = np.nan
        with self.assertRaisesRegex(ValueError, 'Nonfinite'):
            fit_xgb(frame, ['feature'], 'execution', {'n_estimators': 2})


class EvidenceGateTests(unittest.TestCase):
    def result(self):
        cycles = lambda value: [{'signal_date': str(i), 'status': 'complete', 'net_return': value} for i in range(9)]
        return {'selection_diagnostic': {'strategies': {
            'model': {'status': 'complete', 'cycles': cycles(.03)},
            'momentum': {'status': 'complete', 'cycles': cycles(.01)}}},
            'retail': {s: {'status': 'complete', 'net_return': .1} for s in ('base', 'double_costs', 'entry_delayed')},
            'matched_momentum_retail': {s: {'status': 'complete', 'net_return': .05} for s in ('base', 'double_costs', 'entry_delayed')}}

    def test_positive_pairs_still_never_allow_production(self):
        result = paired_check(self.result())
        self.assertTrue(result['supported'])
        self.assertEqual(result['independent_pairs'], 9)
        self.assertAlmostEqual(result['familywise_95pct_interval'][0], .02)
        self.assertFalse(result['production_ready'])

    def test_incomplete_diagnostic_or_cost_failure_blocks_support(self):
        result = self.result()
        result['selection_diagnostic']['strategies']['model']['status'] = 'incomplete'
        self.assertFalse(paired_check(result)['supported'])
        result = self.result()
        result['retail']['double_costs']['net_return'] = .04
        self.assertFalse(paired_check(result)['supported'])


if __name__ == '__main__':
    unittest.main()
