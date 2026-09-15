"""
druid.py — minimal Druid SQL client.

All dashboard reads go through the Druid SQL endpoint so the API is a thin
query/format layer over the serving datasources (screener, price_history,
stock_scores, market_quotes).
"""

import logging

import requests

logger = logging.getLogger(__name__)

DRUID_SQL_URL = "http://localhost:8888/druid/v2/sql"

# Scalar subquery selecting the most recent screener snapshot. Served snapshots
# are append-only, so queries scope to this to get "latest per symbol".
LATEST_SNAPSHOT = "(SELECT MAX(__time) FROM screener)"


class DruidError(RuntimeError):
    pass


def query(sql: str, timeout: float = 30.0) -> list[dict]:
    try:
        resp = requests.post(DRUID_SQL_URL, json={"query": sql}, timeout=timeout)
    except requests.RequestException as e:
        raise DruidError(f"Druid is unreachable: {e}") from e

    if resp.status_code != 200:
        raise DruidError(f"Druid query failed ({resp.status_code}): {resp.text[:400]}")

    return resp.json()


def quote(value: str) -> str:
    """Single-quote a SQL string literal (Druid SQL has no bind params here)."""
    return "'" + str(value).replace("'", "''") + "'"


def datasource_count(datasource: str) -> int | None:
    try:
        rows = query(f'SELECT COUNT(*) AS n FROM "{datasource}"')
        return int(rows[0]["n"]) if rows else 0
    except DruidError:
        return None
