"""
Experiment 2 — end-to-end and per-stage latency (roadmap §4).

Times each pipeline stage, then polls Druid until a fresh `screener` snapshot is
queryable. Repeats N times and reports p50/p95 end-to-end and the
publish->Druid handoff lag. Emits benchmarks/latency/*.json + summary.

Prereq: stack up; Druid Kafka supervisors running.

    python benchmarks/latency.py --repeat 3
"""

import argparse
import os
import time

from common import (
    REPO_ROOT,
    percentile,
    run_pipeline,
    stage_seconds,
    wait_for_fresh_snapshot,
    write_json,
    write_text,
)


def main():
    parser = argparse.ArgumentParser(description="End-to-end latency")
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()

    runs = []
    for i in range(args.repeat):
        print(f"[latency] run {i + 1}/{args.repeat} ...")
        started = time.time()
        result = run_pipeline(full=args.full, limit=args.limit,
                              log_dir=os.path.join(REPO_ROOT, "benchmarks", "latency", "logs"))
        serving_end = started + sum(
            s["seconds"] for s in result["stages"] if s["stage"] in ("ingest", "indicators", "score", "serving")
        )
        fresh = wait_for_fresh_snapshot(timeout=args.timeout)
        run = {
            "stages": result["stages"],
            "total_seconds": round(time.time() - started, 3),
            "druid_ready": fresh["ready"],
            "druid_handoff_seconds": round(time.time() - serving_end, 3),
            "snapshot_time": fresh["snapshot_time"],
        }
        runs.append(run)
        print(f"  total={run['total_seconds']}s handoff={run['druid_handoff_seconds']}s")

    totals = [r["total_seconds"] for r in runs]
    handoffs = [r["druid_handoff_seconds"] for r in runs]
    summary = {
        "config": {"repeat": args.repeat, "limit": args.limit, "full": args.full},
        "end_to_end_p50": percentile(totals, 0.5),
        "end_to_end_p95": percentile(totals, 0.95),
        "handoff_p50": percentile(handoffs, 0.5),
        "handoff_p95": percentile(handoffs, 0.95),
        "runs": runs,
    }
    write_json("latency", f"latency_{int(time.time())}.json", summary)

    lines = [
        "# Latency",
        "",
        f"repeat={args.repeat} limit={args.limit} full={args.full}",
        "",
        "| run | total s | handoff s | stages |",
        "|---|---|---|---|",
    ]
    for r in runs:
        lines.append(
            f"| {r['snapshot_time']} | {r['total_seconds']} | {r['druid_handoff_seconds']} | "
            + ", ".join(f"{k}={v:.1f}" for k, v in stage_seconds({"stages": r["stages"]}).items())
            + " |"
        )
    lines += [
        "",
        f"p50 end-to-end **{summary['end_to_end_p50']:.1f}s**, "
        f"p95 end-to-end **{summary['end_to_end_p95']:.1f}s**; "
        f"p50 handoff **{summary['handoff_p50']:.1f}s**, "
        f"p95 handoff **{summary['handoff_p95']:.1f}s**.",
    ]
    write_text("latency", "summary.md", "\n".join(lines) + "\n")
    print("wrote benchmarks/latency/")


if __name__ == "__main__":
    main()
