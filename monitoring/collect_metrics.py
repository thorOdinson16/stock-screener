"""
collect_metrics.py — lightweight operational metrics snapshot (roadmap §5).

Collects Kafka consumer lag, Druid ingestion lag/segment counts/query latency,
HDFS capacity/health and the latest Airflow pipeline run state into
monitoring/metrics/<timestamp>.json (dependency-light: no Prometheus/Grafana).
Intended to run after each pipeline run or on a schedule.

    python monitoring/collect_metrics.py
"""

import argparse
import json
import os
import re
import subprocess
import time
import urllib.request
from datetime import datetime, timezone

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_DEFAULT_OUT = os.path.join(_REPO_ROOT, "monitoring", "metrics")

_LAG_RE = re.compile(r"^\S+\s+\S+\s+\d+")
_LIVE_DN_RE = re.compile(r"Live datanodes \((\d+)\)")
_DEAD_DN_RE = re.compile(r"Dead datanodes \((\d+)\)")


def pipeline_env() -> dict:
    out = subprocess.check_output(
        ["bash", "-c", f"source {_REPO_ROOT}/config/pipeline.env && env"], text=True
    )
    env = {}
    for line in out.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            env[key] = value
    return env


def total_kafka_lag(text: str) -> int:
    total = 0
    for line in text.splitlines():
        if _LAG_RE.match(line):
            try:
                total += int(line.split()[5])
            except (IndexError, ValueError):
                continue
    return total


def parse_hdfs_report(text: str) -> dict:
    def find(label):
        prefix = f"{label}:"
        for line in text.splitlines():
            line = line.strip()
            if line.startswith(prefix):
                numbers = re.findall(r"\d+", line.split(":", 1)[1])
                if numbers:
                    return int(numbers[0])
        return None

    live = _LIVE_DN_RE.search(text)
    dead = _DEAD_DN_RE.search(text)
    return {
        "capacity_bytes": find("Configured Capacity"),
        "used_bytes": find("DFS Used"),
        "remaining_bytes": find("DFS Remaining"),
        "live_datanodes": int(live.group(1)) if live else None,
        "dead_datanodes": int(dead.group(1)) if dead else None,
    }


def collect_kafka(env) -> dict:
    kafka_home = env.get("KAFKA_HOME", "/home/abhi/kafka")
    cmd = [
        os.path.join(kafka_home, "bin", "kafka-consumer-groups.sh"),
        "--bootstrap-server", env.get("KAFKA_BOOTSTRAP", "localhost:9092"),
        "--all-groups", "--describe",
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return {"total_lag": total_kafka_lag(out.stdout), "returncode": out.returncode}
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}


def druid_sql(query, druid_url, timeout=15):
    payload = json.dumps({"query": query}).encode()
    request = urllib.request.Request(
        f"{druid_url.rstrip('/')}/druid/v2/sql", data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode())


def collect_druid(env) -> dict:
    url = env.get("DRUID_URL", "http://localhost:8888")
    result = {}
    started = time.perf_counter()
    try:
        result["query_latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
        result["segments"] = {
            row["datasource"]: row["segments"]
            for row in druid_sql(
                "SELECT datasource, COUNT(*) AS segments FROM sys.segments "
                "WHERE is_active = 1 GROUP BY datasource",
                url,
            )
        }
        result["segment_query_latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
        latest = druid_sql("SELECT MAX(__time) AS t FROM screener", url)
        result["screener_latest"] = latest[0]["t"] if latest else None
    except Exception as e:  # noqa: BLE001
        result["error"] = str(e)
    return result


def collect_hdfs(env) -> dict:
    hadoop_home = env.get("HADOOP_HOME", "/home/abhi/hadoop")
    try:
        out = subprocess.run(
            [os.path.join(hadoop_home, "bin", "hdfs"), "dfsadmin", "-report"],
            capture_output=True, text=True, timeout=60,
        )
        return parse_hdfs_report(out.stdout)
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}


def collect_airflow(env) -> dict:
    try:
        import sys

        api_dir = os.path.join(_REPO_ROOT, "api")
        if api_dir not in sys.path:
            sys.path.insert(0, api_dir)
        from airflow import (  # noqa: E402  (local module name)
            DAG_MAINTENANCE,
            DAG_ON_DEMAND,
            DAG_RETRAIN,
            list_dag_runs,
        )

        runs = {}
        for dag_id in (DAG_ON_DEMAND, DAG_RETRAIN, DAG_MAINTENANCE):
            try:
                latest = (list_dag_runs(dag_id, limit=1) or [None])[0]
            except Exception:  # noqa: BLE001
                latest = None
            runs[dag_id] = (
                {"state": latest.get("state"),
                 "start_date": latest.get("start_date") or latest.get("logical_date"),
                 "end_date": latest.get("end_date")}
                if latest else None
            )
        return runs
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}


def collect(env=None) -> dict:
    env = env or pipeline_env()
    return {
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "kafka": collect_kafka(env),
        "druid": collect_druid(env),
        "hdfs": collect_hdfs(env),
        "airflow": collect_airflow(env),
    }


def main():
    parser = argparse.ArgumentParser(description="Collect operational metrics")
    parser.add_argument("--out", default=_DEFAULT_OUT)
    args = parser.parse_args()

    snapshot = collect()
    os.makedirs(args.out, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = os.path.join(args.out, f"{stamp}.json")
    with open(path, "w") as fh:
        json.dump(snapshot, fh, indent=2, default=str)

    index_path = os.path.join(args.out, "index.json")
    index = []
    if os.path.exists(index_path):
        try:
            index = json.load(open(index_path))
        except (json.JSONDecodeError, OSError):
            index = []
    index.append({"file": os.path.basename(path), "collected_at": snapshot["collected_at"]})
    with open(index_path, "w") as fh:
        json.dump(index[-100:], fh, indent=2)

    print(f"wrote {path}")


if __name__ == "__main__":
    main()
