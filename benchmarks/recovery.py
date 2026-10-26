"""
Experiment 4 — fault recovery and data completeness (roadmap §4, spec §19/FR-12).

Kills a component mid-run (a few seconds into a chosen pipeline stage), restarts
it, and verifies the pipeline recovers with no lost/duplicated committed data.
Because kill/start commands are environment specific they must be supplied:

    python benchmarks/recovery.py --component kafka --kill-during poll \
        --kill-cmd "pkill -f kafka.Kafka" \
        --start-cmd "$KAFKA_HOME/bin/kafka-server-start.sh -daemon kafka/configs/server.properties"

Without --kill-during, the component is killed before the run starts.

Emits benchmarks/recovery/<component>.json and rebuilds summary.md from every
component result present.

Metrics: recovery time, Kafka consumer lag before/after, Druid datasource counts
before/after (a drop would indicate lost data; a jump beyond expected would
indicate duplication).
"""

import argparse
import glob
import json
import os
import subprocess
import time

from common import (
    BENCH_ROOT,
    REPO_ROOT,
    STAGES,
    druid_sql,
    pipeline_env,
    run_cmd,
    run_pipeline,
    stage_command,
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


def run_with_fault(args, env, log_dir) -> dict:
    """Runs the pipeline stages in order and kills the component `kill_delay`
    seconds into stage `kill_during`. Returns the failed stage (or None)."""
    supervisors = f"{REPO_ROOT}/scripts/druid_supervisors.sh"
    run_cmd(["bash", supervisors, "resume"])
    os.makedirs(log_dir, exist_ok=True)
    try:
        for stage in STAGES:
            cmd, stage_env = stage_command(stage, False, args.limit, False)
            with open(os.path.join(log_dir, f"fault-{stage}.log"), "w") as log:
                proc = subprocess.Popen(
                    cmd, cwd=REPO_ROOT, env={**os.environ, **stage_env},
                    stdout=log, stderr=subprocess.STDOUT,
                )
                if stage == args.kill_during:
                    time.sleep(args.kill_delay)
                    print(f"[recovery] killing {args.component} during {stage} ...")
                    run_cmd(["bash", "-c", args.kill_cmd], env=env)
                try:
                    rc = proc.wait(timeout=args.stage_timeout)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    rc = "timeout"
            if rc != 0:
                return {"failed_stage": stage, "returncode": rc}
        return {"failed_stage": None, "returncode": 0}
    finally:
        run_cmd(["bash", supervisors, "suspend"])


def write_summary() -> None:
    """Rebuilds summary.md from every per-component result file."""
    results = []
    for path in sorted(glob.glob(os.path.join(BENCH_ROOT, "recovery", "*.json"))):
        with open(path) as fh:
            results.append(json.load(fh))
    lines = [
        "# Fault recovery (Experiment 4)", "",
        "Each component is killed mid-run; the run is retried after the component is",
        "restarted. Druid counts are rows per datasource before the faulty run and after",
        "the recovered run (one successful run of `--limit` symbols is expected to add",
        "one snapshot).", "",
        "| component | killed during | run failed | failed stage | recovered | recovery s | "
        "Kafka lag before -> after | screener delta | stock_scores delta | market_quotes delta |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        d = r.get("druid_count_delta", {})
        lines.append(
            f"| {r['component']} | {r.get('kill_during') or 'before run'} | "
            f"{r['pipeline_failed_while_down']} | {r.get('failed_stage')} | {r['recovered']} | "
            f"{r['recovery_seconds']} | {r['before']['kafka']['lag']} -> {r['after']['kafka']['lag']} | "
            f"{d.get('screener')} | {d.get('stock_scores')} | {d.get('market_quotes')} |"
        )
    write_text("recovery", "summary.md", "\n".join(lines) + "\n")


def main():
    parser = argparse.ArgumentParser(description="Fault-recovery experiment")
    parser.add_argument("--component", default="kafka")
    parser.add_argument("--kill-cmd", required=True)
    parser.add_argument("--start-cmd", required=True)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--recovery-timeout", type=int, default=600)
    parser.add_argument("--kill-during", choices=STAGES, default=None,
                        help="Kill the component this far into the run (default: before it)")
    parser.add_argument("--kill-delay", type=float, default=5.0,
                        help="Seconds into --kill-during before the kill")
    parser.add_argument("--stage-timeout", type=int, default=900)
    parser.add_argument("--restart-wait", type=float, default=0.0,
                        help="Seconds to leave the component down before restarting")
    args = parser.parse_args()

    env = pipeline_env()
    before = snapshot(env)

    log_dir = os.path.join(REPO_ROOT, "benchmarks", "recovery", "logs", args.component)

    if args.kill_during:
        fault = run_with_fault(args, env, log_dir)
    else:
        print(f"[recovery] killing {args.component} before the run ...")
        run_cmd(["bash", "-c", args.kill_cmd], env=env)
        time.sleep(3)
        try:
            run_pipeline(limit=args.limit, log_dir=log_dir)
            fault = {"failed_stage": None, "returncode": 0}
        except RuntimeError as e:
            fault = {"failed_stage": str(e).split()[1], "returncode": 1}
    failed = fault["failed_stage"] is not None
    print(f"[recovery] faulty run failed={failed} at {fault['failed_stage']}")
    time.sleep(args.restart_wait)

    print(f"[recovery] restarting {args.component} ...")
    started = time.perf_counter()
    run_cmd(["bash", "-c", args.start_cmd], env=env)

    recovered = False
    while time.perf_counter() - started < args.recovery_timeout:
        try:
            run_pipeline(limit=args.limit, log_dir=log_dir)
            recovered = True
            break
        except RuntimeError:
            time.sleep(10)
    recovery_seconds = time.perf_counter() - started

    after = snapshot(env)
    payload = {
        "component": args.component,
        "kill_during": args.kill_during,
        "kill_delay": args.kill_delay,
        "limit": args.limit,
        "failed_stage": fault["failed_stage"],
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
    write_json("recovery", f"{args.component}.json", payload)
    write_summary()
    print("wrote benchmarks/recovery/")


if __name__ == "__main__":
    main()
