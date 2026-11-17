"""Core API behaviour: error mapping, input validation and generated SQL.

Needs `httpx` (FastAPI's TestClient). Run with
    python -m pytest tests/test_api_main.py
"""
from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

HAVE_CLIENT = importlib.util.find_spec("httpx") is not None and importlib.util.find_spec("fastapi") is not None


@unittest.skipUnless(HAVE_CLIENT, "requires fastapi and httpx")
class CoreApiTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        from api.main import app
        self.client = TestClient(app, raise_server_exceptions=False)

    def test_druid_outage_is_a_503_with_a_message_not_a_500(self):
        from api.druid import DruidError
        with patch("api.main.query", side_effect=DruidError("Druid is unreachable: refused")):
            response = self.client.get("/api/market/overview")
        self.assertEqual(response.status_code, 503)
        self.assertIn("unreachable", response.json()["detail"])

    def test_non_finite_filters_never_reach_druid(self):
        with patch("api.main.query") as query:
            for value in ("nan", "inf", "-inf"):
                response = self.client.get("/api/screener", params={"min_pe": value})
                self.assertEqual(response.status_code, 422, value)
        query.assert_not_called()

    def test_screener_sql_quotes_strings_and_formats_numbers(self):
        captured = []
        with patch("api.main.query", side_effect=lambda sql, *a, **k: captured.append(sql) or []):
            response = self.client.get("/api/screener", params={
                "industry": "O'Brien & Sons", "min_pe": "10", "min_rsi": "40.5"})
        self.assertEqual(response.status_code, 200)
        sql = captured[0]
        self.assertIn("'O''Brien & Sons'", sql)
        self.assertIn("pe_ratio >= 10.0", sql)
        self.assertIn("rsi_14 >= 40.5", sql)

    def test_history_accepts_the_ranges_the_dashboard_sends(self):
        today = datetime.now(timezone.utc).date()
        for label, days in (("3mo", 90), ("6mo", 180), ("1y", 365), ("2y", 730)):
            captured = []
            with patch("api.main.query", side_effect=lambda sql, *a, **k: captured.append(sql) or []):
                self.assertEqual(self.client.get("/api/stocks/RELIANCE/history", params={"range": label}).status_code, 200)
            start = datetime.fromisoformat(captured[0].split("TIMESTAMP '")[1].split("'")[0]).date()
            self.assertAlmostEqual((today - start).days, days, delta=1, msg=label)

    def test_pipeline_rejects_a_bad_universe_limit_before_touching_airflow(self):
        with patch("api.pipeline.af.find_active_run") as active:
            for bad in (0, -5, 10**9):
                response = self.client.post("/api/pipeline/run", json={"universe_limit": bad})
                self.assertEqual(response.status_code, 422, bad)
        active.assert_not_called()

    def test_pipeline_run_passes_a_valid_limit_and_blocks_overlap(self):
        run = {"dag_run_id": "r1", "state": "queued", "conf": {}}
        with patch("api.pipeline.af.find_active_run", return_value=None), \
             patch("api.pipeline.af.trigger_dag", return_value=run) as trigger, \
             patch("api.pipeline.af.ui_base_url", return_value="http://airflow"):
            response = self.client.post("/api/pipeline/run", json={"universe_limit": 50})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(trigger.call_args.args[1]["universe_limit"], 50)
        active = {"dag_id": "screening_retrain", "dag_run_id": "x", "state": "running"}
        with patch("api.pipeline.af.find_active_run", return_value=active), \
             patch("api.pipeline.af.trigger_dag") as trigger, \
             patch("api.pipeline.af.ui_base_url", return_value="http://airflow"):
            for path in ("/api/pipeline/run", "/api/pipeline/retrain", "/api/pipeline/maintenance"):
                self.assertEqual(self.client.post(path, json={}).status_code, 409, path)
        trigger.assert_not_called()


class UniverseCacheTests(unittest.TestCase):
    def test_changed_cache_file_is_picked_up_without_a_restart(self):
        import json
        import os
        import tempfile
        from api import universe
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cache.json"
            write = lambda name: path.write_text(json.dumps({"stocks": [
                {"yf_symbol": "A.NS", "company_name": name, "industry": "X"}]}))
            with patch.object(universe, "_CACHE_PATH", str(path)):
                write("Old Name")
                self.assertEqual(universe.metadata_for("A.NS")["company_name"], "Old Name")
                write("New Name")
                os.utime(path, ns=(1, path.stat().st_mtime_ns + 10**9))
                self.assertEqual(universe.metadata_for("A.NS")["company_name"], "New Name")


if __name__ == "__main__":
    unittest.main()
