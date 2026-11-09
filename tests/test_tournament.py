"""Maturity, common-input, ranking API and validation-only selection checks."""

from datetime import date, timedelta
from pathlib import Path
import sys
import unittest
import tempfile
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ml.evaluation.tournament_models import training_rows, rank_labels, fit_model, predict, sequences, make_tcn, SequenceModel
from ml.evaluation.model_tournament import choose_paper_candidate, signals_for


class TournamentTests(unittest.TestCase):
    def test_maturity_purge_uses_scheduled_not_early_stop_exit(self):
        frame = pd.DataFrame({"symbol": ["A", "B"], "trade_date": ["2025-01-01"] * 2,
                              "net_return": [-.08, .1], "scheduled_exit_date": ["2025-02-01", "2025-01-20"],
                              "actual_exit_date": ["2025-01-02", "2025-01-20"]})
        train = training_rows(frame, "execution", ["2025-01-01"], "2025-01-25")
        self.assertEqual(train.symbol.tolist(), ["B"])

    def test_ties_are_not_arbitrarily_ranked_by_symbol(self):
        frame = pd.DataFrame({"trade_date": ["2025-01-01"] * 3, "net_return": [-.08, -.08, .1]})
        y = rank_labels(frame, "execution")
        self.assertEqual(y[0], y[1])
        self.assertGreater(y[2], y[0])

    def test_real_model_apis_accept_grouped_dates_and_produce_finite_scores(self):
        frame = pd.DataFrame([{"symbol": str(i), "trade_date": f"2025-01-{d:02}",
                               "x": i / 20, "net_return": i / 100} for d in range(1, 21) for i in range(20)])
        for family, params in [("linear", {"alpha": 1}), ("lightgbm", {"num_leaves": 7, "n_estimators": 5}),
                               ("catboost", {"depth": 2, "iterations": 5})]:
            with self.subTest(family=family):
                model = fit_model(family, params, frame, ["x"], "execution")
                scores = predict(model, frame, ["x"])
                self.assertEqual(len(scores), len(frame))
                self.assertTrue(np.isfinite(scores).all())
                self.assertGreater(scores[19], scores[0])

    def test_sequence_windows_use_past_only_and_reject_calendar_gaps(self):
        days = [(date(2025, 1, 1) + timedelta(days=i)).isoformat() for i in range(6)]
        frame = pd.DataFrame({"symbol": ["A"] * 5, "trade_date": [days[i] for i in [0, 1, 2, 4, 5]], "x": [0, 1, 2, 4, 5]})
        tensor, valid = sequences(frame, ["x"], days, 3)
        self.assertEqual(valid.tolist(), [False, False, True, False, False])
        np.testing.assert_array_equal(tensor[2, 0], [0, 1, 2])

    def test_causal_convolution_does_not_use_future_positions(self):
        import torch
        torch.manual_seed(42)
        layer = make_tcn(1)[0]
        original = torch.arange(20, dtype=torch.float32).reshape(1, 1, 20)
        changed = original.clone()
        changed[:, :, 10:] = 1000
        torch.testing.assert_close(layer(original)[:, :, :10], layer(changed)[:, :, :10])

    def test_sequence_scaling_is_train_only_and_frozen_weights_reload(self):
        import torch
        values = np.random.default_rng(42).normal(size=(40, 2, 20)).astype(np.float32)
        values[30:] += 1000
        model = SequenceModel(epochs=1).fit(values, np.arange(30), np.linspace(-.5, .5, 30), np.ones(30))
        np.testing.assert_allclose(model.mean, values[:30].mean(axis=(0, 2), keepdims=True))
        expected = model.predict(values, np.arange(30, 40))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "frozen.pt"
            torch.save({"state_dict": model.model.state_dict(), "mean": torch.from_numpy(model.mean), "scale": torch.from_numpy(model.scale)}, path)
            saved = torch.load(path, weights_only=True, map_location="cpu")
            restored = SequenceModel()
            restored.model = make_tcn(2)
            restored.model.load_state_dict(saved["state_dict"])
            restored.mean, restored.scale = saved["mean"].numpy(), saved["scale"].numpy()
            np.testing.assert_allclose(restored.predict(values, np.arange(30, 40)), expected)

    def test_candidate_selection_only_uses_validation_and_excludes_unverified_fundamentals(self):
        def report(value):
            return {"retail": {name: {"status": "complete", "net_return": value,
                                      "max_drawdown": -.1, "closed_trades": 50}
                               for name in ("base", "double_costs", "entry_delayed")}}
        metadata = {name: {"features": feature, "target": "execution",
                           "fold_quality": [{"mean_daily_rank_ic": .02}] * 3}
                    for name, feature in [("price", "price_only"), ("fundamentals", "legacy_all")]}
        price = {**report(.2), "matched_momentum_retail": report(.1)["retail"]}
        fundamentals = {**report(10), "matched_momentum_retail": report(.1)["retail"]}
        winner, reasons = choose_paper_candidate({"price": price, "fundamentals": fundamentals}, metadata)
        self.assertEqual(winner, "price")
        self.assertIn("Unverified annual publication dates", reasons["fundamentals"])
        weak = {**report(.05), "matched_momentum_retail": report(.1)["retail"]}
        self.assertIsNone(choose_paper_candidate({"price": weak}, {"price": metadata["price"]})[0])

    def test_momentum_control_cannot_use_stocks_or_dates_outside_candidate_pool(self):
        day = "2025-01-02"
        prediction = pd.DataFrame({"symbol": ["A", "B"], "trade_date": [day] * 2, "score": [.9, .1]})
        stats = {(s, day): {"symbol": s, "trade_date": day, "close": 100,
                            "volume_avg_20d": 1000000, "volatility_20d": .02, "momentum": m}
                 for s, m in [("A", .1), ("B", .2), ("C", 10.)]}
        model = signals_for(prediction, stats, [])
        control = signals_for(prediction, stats, [], momentum=True)
        self.assertEqual(set(model), set(control))
        self.assertEqual([r["symbol"] for r in model[day]], ["A", "B"])
        self.assertEqual([r["symbol"] for r in control[day]], ["B", "A"])


if __name__ == "__main__":
    unittest.main()
