"""Remaining-plan feature, provenance, probability and learner checks."""

from datetime import date, timedelta
from pathlib import Path
import sys
import tempfile
import unittest
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ml.evaluation.research_context import add_context
from ml.evaluation.completion_models import Learner, fit_sigmoid
from ml.evaluation.completion_research import add_targets
from ml.evaluation.completion_research import probability_report
from ml.evaluation.verified_financials import attach, parse_filing


class CompletionTests(unittest.TestCase):
    def test_context_ignores_future_prices_and_excludes_own_sector_value(self):
        days = [(date(2025, 1, 1) + timedelta(days=i)).isoformat() for i in range(230)]
        frame = pd.DataFrame([{"symbol": s, "trade_date": d, "industry": "Sector"} for s in ("A", "B", "C") for d in days])
        bars = {(s, d): {"close": 100 + i + (i % 3)} for s in ("A", "B", "C", "^NSEI") for i, d in enumerate(days)}
        statistics = {(s, d): {"momentum": m} for s, m in [("A", .1), ("B", .2), ("C", .3)] for d in days}
        original = add_context(frame, bars, days, statistics)
        self.assertAlmostEqual(original[original.symbol == "A"].sector_momentum_63d.iloc[-1], .25)
        bars[("^NSEI", days[-1])]["close"] = 10000
        changed = add_context(frame, bars, days, statistics)
        pd.testing.assert_frame_equal(original[original.trade_date < days[-1]], changed[changed.trade_date < days[-1]])

    def test_verified_features_wait_until_publication_and_expire(self):
        frame = pd.DataFrame({"symbol": ["A"] * 3, "trade_date": ["2025-02-10", "2025-02-11", "2026-02-11"]})
        rows = [{"symbol": "A", "available_date": "2025-02-11", "published_at": "2025-02-10T18:00:00+05:30", "period_end": "2024-12-31",
                 "verified_profit_margin": .1, "verified_revenue_yoy": .2, "verified_profit_yoy": .3, "source": "https://example.org/filing.xml"}]
        result = attach(frame, rows)
        self.assertTrue(pd.isna(result.verified_profit_margin.iloc[0]))
        self.assertAlmostEqual(result.verified_profit_margin.iloc[1], .1)
        self.assertTrue(pd.isna(result.verified_profit_margin.iloc[2]))

    def test_real_native_categories_and_binary_models_handle_unseen_category(self):
        frame = pd.DataFrame([{"symbol": str(i), "trade_date": f"2025-01-{d:02}", "industry": "A" if i % 2 else "B",
                               "x": i / 20, "net_return": (i - 10) / 100, "profit_label": int(i > 10)}
                              for d in range(1, 21) for i in range(20)])
        for family, objective in [("linear", "rank"), ("catboost", "rank"), ("catboost", "profit"), ("lightgbm", "profit")]:
            model = Learner(family, objective, ["x"], True).fit(frame)
            test = frame.iloc[:3].copy()
            test["industry"] = "Unseen industry"
            values = model.predict(test)
            self.assertTrue(np.isfinite(values).all())
            if objective == "profit":
                self.assertTrue(((values >= 0) & (values <= 1)).all())

    def test_sigmoid_fit_requires_separate_sufficient_sample(self):
        rows = [{"signal_date": f"2025-{1+i//28:02}-{1+i%28:02}", "logit_probability": float(j - 5), "target": int(j > 5)}
                for i in range(60) for j in range(10)]
        fit = fit_sigmoid(rows, "target")
        self.assertEqual(fit["fit_dates"], 60)
        self.assertGreater(fit["coefficients"][1], 0)
        self.assertIsNone(fit_sigmoid(rows[:20], "target"))

    def test_calibration_purges_outcomes_not_mature_before_assessment(self):
        days = [(date(2025, 1, 1) + timedelta(days=i)).isoformat() for i in range(140)]
        rows = [{"symbol": str(s), "trade_date": days[i], "score": .8 if s % 2 else .2,
                 "fold": 3 if i < 80 else 4, "eligible": True, "profit_label": s % 2,
                 "scheduled_exit_date": days[i + 22]} for i in range(110) for s in range(4)]
        frame = pd.DataFrame(rows)
        result = probability_report(frame[["symbol", "trade_date", "score", "fold"]], frame, "profit", days[80], days[109])
        self.assertLess(result["calibration_last_maturity"], days[80])
        self.assertFalse(result["production_ready"])

    def test_xbrl_uses_primary_quarter_not_same_dated_cumulative_context(self):
        xml = '''<x:xbrl xmlns:x="http://www.xbrl.org/2003/instance" xmlns:f="urn:financial">
        <x:context id="OneD"><x:period><x:startDate>2024-10-01</x:startDate><x:endDate>2024-12-31</x:endDate></x:period></x:context>
        <x:context id="FourD"><x:period><x:startDate>2024-10-01</x:startDate><x:endDate>2024-12-31</x:endDate></x:period></x:context>
        <x:unit id="INR"><x:measure>iso4217:INR</x:measure></x:unit>
        <f:Symbol contextRef="OneD">A</f:Symbol><f:NatureOfReportStandaloneConsolidated contextRef="OneD">Consolidated</f:NatureOfReportStandaloneConsolidated>
        <f:DateOfEndOfReportingPeriod contextRef="OneD">2024-12-31</f:DateOfEndOfReportingPeriod>
        <f:RevenueFromOperations contextRef="OneD" unitRef="INR">100</f:RevenueFromOperations><f:ProfitLossForPeriod contextRef="OneD" unitRef="INR">10</f:ProfitLossForPeriod>
        <f:RevenueFromOperations contextRef="FourD" unitRef="INR">300</f:RevenueFromOperations><f:ProfitLossForPeriod contextRef="FourD" unitRef="INR">60</f:ProfitLossForPeriod></x:xbrl>'''
        record = {"symbol": "A.NS", "period_end": "31-Dec-2024", "published_times": ["10-Feb-2025 18:00:00"], "source": "https://example.org/original.xml", "index_source": "https://example.org/timestamps"}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.xml"
            path.write_text(xml)
            parsed = parse_filing(record, path, "2025-02-11")
            self.assertAlmostEqual(parsed["verified_profit_margin"], .1)
            self.assertIsNone(parsed["verified_revenue_yoy"])
            self.assertEqual(parsed["available_date"], "2025-02-11")
            with self.assertRaises(ValueError):
                parse_filing(record, path, "2025-02-09")


if __name__ == "__main__":
    unittest.main()
