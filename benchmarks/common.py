"""
common.py — shared helpers for the benchmark/experiment scripts under benchmarks/.

Every experiment records the git commit, its config and timestamps so runs are
reproducible (roadmap §4). These scripts drive the live stack (HDFS/Kafka/
SeaTunnel/Spark/Druid), so they are operational: run them with the stack up.
"""

import json
import os
import subprocess
import time
import urllib.request
from datetime import datetime, timezone

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BENCH_ROOT = os.path.join(REPO_ROOT, "benchmarks")

# Pipeline stages in on-demand order (mirrors scripts/run_once.sh).
STAGES = ["preflight", "poll", "ingest", "indicators", "score", "serving", "wait_druid"]


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def git_commit() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "-C", REPO_ROOT, "rev-parse", "HEAD"], stderr=subprocess.DEVNULL
        ).decode().strip()
    except Exception:  # noqa: BLE001
        return None


def pipeline_env() -> dict:
    """Exports from config/pipeline.env (resolved by bash, so defaults apply)."""
    out = subprocess.check_output(
        ["bash", "-c", f"source {REPO_ROOT}/config/pipeline.env && env"],
        text=True,
    )
    env = {}
    for line in out.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            env[key] = value
    return env


def run_cmd(cmd, env=None, log_path=None, timeout=None) -> dict:
    """Runs a command, returns {returncode, seconds, log} and tees stdout/stderr
    to `log_path` when given."""
    started = time.perf_counter()
    full_env = dict(os.environ)
    if env:
        full_env.update({k: str(v) for k, v in env.items() if v is not None})
    proc = subprocess.run(
        cmd, cwd=REPO_ROOT, env=full_env, capture_output=True, text=True, timeout=timeout
    )
    seconds = time.perf_counter() - started
    output = (proc.stdout or "") + (proc.stderr or "")
    if log_path:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "w") as fh:
            fh.write(output)
    return {
        "returncode": proc.returncode,
        "seconds": seconds,
        "log": log_path,
        "tail": output[-2000:],
    }


def stage_command(stage: str, full: bool, limit, publish_history: bool, extra_env=None):
    env = {
        "FULL_RUN": "1" if full else "0",
        "UNIVERSE_LIMIT": "" if limit is None else str(limit),
        "PUBLISH_HISTORY": "1" if publish_history else "0",
    }
    if extra_env:
        env.update(extra_env)
    script = f"{REPO_ROOT}/scripts/{stage}.sh"
    if stage == "wait_druid":
        cmd = ["bash", script, os.environ.get("WAIT_DRUID_TIMEOUT", "240")]
    else:
        cmd = ["bash", script]
    return cmd, env


def run_pipeline(full=False, limit=None, publish_history=False, log_dir=None,
                 extra_env=None) -> dict:
    """Runs the on-demand pipeline stage by stage, timing each one. Raises if a
    stage fails so a benchmark never records a partial run as success."""
    stages = []
    started = time.perf_counter()
    for stage in STAGES:
        cmd, env = stage_command(stage, full, limit, publish_history, extra_env)
        log_path = os.path.join(log_dir, f"{stage}.log") if log_dir else None
        result = run_cmd(cmd, env=env, log_path=log_path)
        stages.append({"stage": stage, "seconds": result["seconds"],
                       "returncode": result["returncode"]})
        if result["returncode"] != 0:
            raise RuntimeError(
                f"stage {stage} failed (rc={result['returncode']}): {result['tail']}"
            )
    return {"stages": stages, "total_seconds": time.perf_counter() - started}


def druid_sql(query: str, druid_url: str | None = None, timeout: int = 20):
    url = (druid_url or os.environ.get("DRUID_URL", "http://localhost:8888")).rstrip("/")
    payload = json.dumps({"query": query}).encode()
    request = urllib.request.Request(
        f"{url}/druid/v2/sql", data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode())


def druid_max_time(datasource: str = "screener", druid_url: str | None = None):
    rows = druid_sql(f"SELECT MAX(__time) AS t FROM {datasource}", druid_url)
    return rows[0]["t"] if rows else None


def wait_for_fresh_snapshot(max_age_seconds=300, timeout=300, poll_seconds=5,
                            druid_url=None) -> dict:
    """Polls Druid until it serves a `screener` snapshot younger than
    `max_age_seconds`; returns the observed latency (seconds since the wait
    began) and the snapshot timestamp."""
    started = time.time()
    last = None
    while time.time() - started < timeout:
        last = druid_max_time("screener", druid_url)
        if last:
            ts = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
            if (datetime.now(timezone.utc) - ts).total_seconds() < max_age_seconds:
                return {"ready": True, "latency_seconds": time.time() - started,
                        "snapshot_time": last}
        time.sleep(poll_seconds)
    return {"ready": False, "latency_seconds": time.time() - started, "snapshot_time": last}


def percentile(values, p: float) -> float:
    values = sorted(v for v in values if v is not None)
    if not values:
        return float("nan")
    if len(values) == 1:
        return float(values[0])
    rank = (len(values) - 1) * p
    low, high = int(rank), min(int(rank) + 1, len(values) - 1)
    frac = rank - low
    return float(values[low] * (1 - frac) + values[high] * frac)


def write_json(dirname: str, filename: str, payload: dict) -> str:
    path = os.path.join(BENCH_ROOT, dirname, filename)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        json.dump({"recorded_at": iso_now(), "git_commit": git_commit(), **payload},
                  fh, indent=2, default=str)
    return path


def write_text(dirname: str, filename: str, text: str) -> str:
    path = os.path.join(BENCH_ROOT, dirname, filename)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(text)
    return path


def stage_seconds(result: dict) -> dict:
    return {s["stage"]: round(s["seconds"], 3) for s in result["stages"]}
