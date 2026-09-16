"""
End-to-end smoke test (opt-in; requires the full stack up).

Runs the on-demand pipeline against a small universe, then asserts Druid serves
a fresh screener snapshot and the API responds. Skipped unless RUN_E2E=1 so it
never runs in the default unit-test pass.

    RUN_E2E=1 python tests/test_pipeline_e2e.py --limit 10
"""

import argparse
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(_ROOT, "benchmarks"))

from common import druid_sql, run_pipeline  # noqa: E402

API_URL = os.environ.get("API_URL", "http://localhost:8000")


def api(path: str):
    import urllib.request

    with urllib.request.urlopen(f"{API_URL}{path}", timeout=15) as response:
        return response.status, response.read()


def run(limit: int):
    print(f"[e2e] running pipeline with limit={limit} ...")
    run_pipeline(limit=limit, log_dir=os.path.join(_ROOT, "benchmarks", "e2e-logs"))

    rows = druid_sql("SELECT COUNT(*) AS n FROM screener")
    count = rows[0]["n"] if rows else 0
    assert count > 0, "Druid has no screener rows after the run"
    print(f"[e2e] Druid screener rows: {count}")

    status, body = api("/api/health")
    assert status == 200, f"API health returned {status}"
    print(f"[e2e] API health OK ({len(body)} bytes)")
    print("[e2e] PASS")


def main():
    if os.environ.get("RUN_E2E") != "1":
        print("SKIP test_pipeline_e2e (set RUN_E2E=1 with the stack up)")
        return 0
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()
    run(args.limit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
