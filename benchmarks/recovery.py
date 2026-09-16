"""
Experiment 4 — fault recovery and data completeness (roadmap §4, spec §19/FR-12).

Kills a component mid-run, restarts it, and verifies the pipeline recovers with
no lost/duplicated committed data. Because kill/start commands are environment
specific they must be supplied:

    python benchmarks/recovery.py --component kafka \
        --kill-cmd "pkill -f kafka.Kafka" \
        --start-cmd "$KAFKA_HOME/bin/kafka-server-start.sh -daemon $KAFKA_HOME/config/server.properties"

Emits benchmarks/recovery/recovery.json + summary.md.

Metrics: recovery time, Kafka consumer lag before/after, Druid datasource counts
before/after (a drop would indicate lost data; a jump beyond expected would
indicate duplication).
"""

import argparse
import os
import time

from common import (
    REPO_ROOT,
    druid_sql,
    pipeline_env,
    run_cmd,
    run_pipeline,
    write_json,
    write_text,
)

DATASOURCES = ["screener", "stock_scores", "price_history", "market_quotes"]


def total_lag(text: str):
    """Sums the LAG column of `kafka-consumer-groups --describe` output."""
    total, rows = 0, 0
    for line in text.splitlines():
        parts = line.split()
        if not parts or parts[0] in ("GROUP", "Consumer", "Note:"):
            continue
        try:
            total += int(parts[5])
            rows += 1
        except (IndexError, ValueError):
            continue
    return total, rows


def kafka_lag(env) -> dict:
    kafka_home = env.get("KAFKA_HOME", "/home/abhi/kafka")
    result = run_cmd([
        os.path.join(kafka_home, "bin", "kafka-consumer-groups.sh"),
        "--bootstrap-server", env.get("KAFKA_BOOTSTRAP", "localhost:9092"),
        "--all-groups", "--describe",
    ])
    lag, rows = total_lag(result["tail"])
    return {"lag": lag, "partitions": rows, "returncode": result["returncode"]}


def druid_counts() -> dict:
    counts = {}
    for ds in DATASOURCES:
        try:
            rows = druid_sql(f"SELECT COUNT(*) AS n FROM {ds}")
            counts[ds] = rows[0]["n"] if rows else None
        except Exception:  # noqa: BLE001 — datasource may not exist yet
            counts[ds] = None
    return counts


def snapshot(env) -> dict:
    return {"kafka": kafka_lag(env), "druid": druid_counts()}


def main():
    parser = argparse.ArgumentParser(description="Fault-recovery experiment")
    parser.add_argument("--component", default="kafka")
    parser.add_argument("--kill-cmd", required=True)
    parser.add_argument("--start-cmd", required=True)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--recovery-timeout", type=int, default=600)
    args = parser.parse_args()

    env = pipeline_env()
    before = snapshot(env)

    print(f"[recovery] killing {args.component} ...")
    run_cmd(["bash", "-c", args.kill_cmd], env=env)
    time.sleep(3)

    failed = False
    try:
        run_pipeline(limit=args.limit, log_dir=os.path.join(REPO_ROOT, "benchmarks", "recovery", "logs"))
    except RuntimeError:
        failed = True

    print(f"[recovery] restarting {args.component} ...")
    started = time.perf_counter()
    run_cmd(["bash", "-c", args.start_cmd], env=env)

    recovered = False
    while time.perf_counter() - started < args.recovery_timeout:
        try:
            run_pipeline(limit=args.limit,
                         log_dir=os.path.join(REPO_ROOT, "benchmarks", "recovery", "logs"))
            recovered = True
            break
        except RuntimeError:
            time.sleep(10)
    recovery_seconds = time.perf_counter() - started

    after = snapshot(env)
    payload = {
        "component": args.component,
        "pipeline_failed_while_down": failed,
        "recovered": recovered,
        "recovery_seconds": round(recovery_seconds, 1),
        "before": before,
        "after": after,
        "druid_count_delta": {
            ds: (
                None if before["druid"].get(ds) is None or after["druid"].get(ds) is None
                else after["druid"][ds] - before["druid"][ds]
            )
            for ds in DATASOURCES
        },
    }
    write_json("recovery", "recovery.json", payload)

    lines = [
        f"# Fault recovery: {args.component}", "",
        f"- pipeline failed while down: {failed}",
        f"- recovered: {recovered} in {recovery_seconds:.1f}s",
        f"- Kafka lag before/after: {before['kafka']['lag']} -> {after['kafka']['lag']}",
        "",
        "| datasource | before | after | delta |",
        "|---|---|---|---|",
    ]
    for ds in DATASOURCES:
        lines.append(
            f"| {ds} | {before['druid'].get(ds)} | {after['druid'].get(ds)} | "
            f"{payload['druid_count_delta'][ds]} |"
        )
    write_text("recovery", "summary.md", "\n".join(lines) + "\n")
    print("wrote benchmarks/recovery/")


if __name__ == "__main__":
    main()
