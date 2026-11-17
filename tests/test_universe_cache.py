"""
The universe cache is hash-pinned by frozen research manifests, so a normal run
must never rewrite it. Only a missing cache or an explicit refresh may fetch.
"""
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "poller"))

from universe import universe  # noqa: E402
from universe.universe import Stock  # noqa: E402


def stock(symbol):
    return Stock(f"{symbol} Ltd", "Sector", symbol, f"{symbol}.NS", "INE0")


class UniverseCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "universe_cache.json"
        self.patch = patch.object(universe, "CACHE_PATH", self.path)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def write(self, symbols, age_days):
        payload = {"fetched_at": time.time() - age_days * 86400,
                   "stocks": [s.__dict__ for s in map(stock, symbols)]}
        self.path.write_text(json.dumps(payload, indent=2))
        return self.path.read_bytes()

    def test_an_old_cache_is_used_and_never_rewritten(self):
        original = self.write(["A", "B"], age_days=400)
        with patch.object(universe, "_fetch_from_nse", side_effect=AssertionError("must not fetch")):
            result = universe.get_universe()
        self.assertEqual([s.yf_symbol for s in result], ["A.NS", "B.NS"])
        self.assertEqual(self.path.read_bytes(), original)  # byte-identical: hash unchanged

    def test_a_very_old_cache_only_warns(self):
        self.write(["A"], age_days=400)
        with self.assertLogs(universe.logger, level="WARNING") as logs:
            universe.get_universe()
        self.assertIn("--refresh", logs.output[0])

    def test_a_recent_cache_does_not_warn(self):
        self.write(["A"], age_days=3)
        with self.assertNoLogs(universe.logger, level="WARNING"):
            universe.get_universe()

    def test_a_missing_cache_is_fetched_and_written(self):
        with patch.object(universe, "_fetch_from_nse", return_value=[stock("A")]) as fetch:
            result = universe.get_universe()
        fetch.assert_called_once()
        self.assertEqual([s.yf_symbol for s in result], ["A.NS"])
        self.assertTrue(self.path.exists())

    def test_an_explicit_refresh_fetches_and_reports_the_difference(self):
        self.write(["A", "B"], age_days=1)
        with patch.object(universe, "_fetch_from_nse", return_value=[stock("B"), stock("C")]):
            with patch("builtins.print") as printed:
                self.assertEqual(universe.refresh(), 0)
        text = "\n".join(str(c.args[0]) for c in printed.call_args_list)
        self.assertIn("+1 added, -1 removed", text)
        self.assertIn("C.NS", text)
        self.assertIn("pinned_sources.py --verify", text)
        self.assertEqual({s["yf_symbol"] for s in json.loads(self.path.read_text())["stocks"]}, {"B.NS", "C.NS"})

    def test_a_failed_fetch_falls_back_to_the_existing_cache(self):
        original = self.write(["A"], age_days=1)
        import requests
        with patch.object(universe, "_fetch_from_nse", side_effect=requests.ConnectionError("down")):
            result = universe.get_universe(force_refresh=True)
        self.assertEqual([s.yf_symbol for s in result], ["A.NS"])
        self.assertEqual(self.path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
