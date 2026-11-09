"""Mock Yahoo and Kafka; run with poller/.venv/bin/python."""

from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "poller"))
import backfill
from universe.universe import Stock


class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.stock = Stock("Company", "Sector", "A", "A.NS", "")

    def test_fetch_requests_repairs_and_persists_observed_actions(self):
        frame = pd.DataFrame({"Open": [100., 50.], "High": [101., 51.], "Low": [99., 49.],
                              "Close": [100., 50.], "Volume": [1000, 2000], "Stock Splits": [0, 2.],
                              "Dividends": [1., 0]}, index=pd.date_range("2026-01-01", periods=2))
        with tempfile.TemporaryDirectory() as temporary, patch.object(backfill.yf, "Ticker") as ticker:
            ticker.return_value.history.return_value = frame
            rows = backfill.fetch_history(self.stock, "1y", "1d", actions_dir=temporary)
            self.assertEqual(len(rows), 2)
            kwargs = ticker.return_value.history.call_args.kwargs
            self.assertTrue(kwargs["repair"] and kwargs["actions"] and kwargs["auto_adjust"])
            record = json.loads((Path(temporary) / "A.NS.json").read_text())
            self.assertEqual(record["price_basis"], "provider_adjusted")
            self.assertEqual({a["kind"] for a in record["events"]}, {"dividend", "split"})

    def test_basis_change_refreshes_history_before_publishing(self):
        overlap = [{"symbol": "A.NS", "trade_date": f"2026-01-0{i}", "close": 50.} for i in (1, 2)]
        full = [{"symbol": "A.NS", "trade_date": "2025-01-01", "close": 40.}] + overlap
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "watermarks.json").write_text(json.dumps({"A.NS": "2026-01-02"}))
            (directory / "refs.json").write_text(json.dumps({"A.NS": {"earliest": "2025-01-01", "closes": {"2026-01-01": 100., "2026-01-02": 100.}}}))
            arguments = ["backfill.py", "--watermarks", str(directory / "watermarks.json"), "--references", str(directory / "refs.json"), "--delay", "0"]
            with patch.object(sys, "argv", arguments), patch.object(backfill, "get_universe", return_value=[self.stock]), patch.object(backfill, "fetch_history", side_effect=[overlap, full]) as fetch, patch.object(backfill, "MarketDataProducer") as producer:
                published = []
                producer.return_value.publish_many.side_effect = lambda topic, rows: published.append((topic, list(rows)))
                backfill.main()
                self.assertEqual(fetch.call_args_list[1].kwargs["start"], "2025-01-01")
                self.assertEqual(published, [(backfill.DAILY_TOPIC, full)])

    def test_failed_reconciliation_never_publishes_mixed_basis(self):
        overlap = [{"symbol": "A.NS", "trade_date": f"2026-01-0{i}", "close": 50.} for i in (1, 2)]
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "w.json").write_text(json.dumps({"A.NS": "2026-01-02"}))
            (directory / "r.json").write_text(json.dumps({"A.NS": {"earliest": "2025-01-01", "closes": {"2026-01-01": 100., "2026-01-02": 100.}}}))
            with patch.object(sys, "argv", ["backfill.py", "--watermarks", str(directory / "w.json"), "--references", str(directory / "r.json")]), patch.object(backfill, "get_universe", return_value=[self.stock]), patch.object(backfill, "fetch_history", side_effect=[overlap, []]), patch.object(backfill, "MarketDataProducer") as producer:
                with self.assertRaises(RuntimeError):
                    backfill.main()
                producer.return_value.publish_many.assert_not_called()


if __name__ == "__main__":
    unittest.main()
