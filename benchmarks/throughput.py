"""
Experiment 1 — throughput vs universe size (roadmap §4).

Runs the on-demand pipeline at several universe sizes and records the wall time
of every stage. Emits benchmarks/throughput/{size}.json and a markdown summary.

Prereq: stack up; a model is selected (ml/models/selected.json).

    python benchmarks/throughput.py --sizes 50 200 500
"""

import argparse
import os

from common import REPO_ROOT, run_pipeline, stage_seconds, write_json, write_text


def main():
    parser = argparse.ArgumentParser(description="Throughput vs universe size")
    parser.add_argument("--sizes", type=int, nargs="+", default=[50, 200, 500])
    parser.add_argument("--full", action="store_true", help="quotes + fundamentals")
    args = parser.parse_args()

    records = []
    stage_names = None
    for size in args.sizes:
        print(f"[throughput] universe={size} ...")
        result = run_pipeline(full=args.full, limit=size,
                              log_dir=os.path.join(REPO_ROOT, "benchmarks", "throughput", "logs"))
        stages = stage_seconds(result)
        record = {
            "universe_size": size,
            "config": {"full": args.full, "limit": size},
            "total_seconds": round(result["total_seconds"], 3),
            "stages": result["stages"],
            "poll_symbols_per_sec": (
                round(size / stages["poll"], 3) if stages.get("poll") else None
            ),
        }
        records.append(record)
        stage_names = [s["stage"] for s in result["stages"]]
        write_json("throughput", f"{size}.json", record)
        print(f"  total={record['total_seconds']}s {stages}")

    lines = [
        "# Throughput",
        "",
        "| universe | total s | " + " | ".join(stage_names) + " | poll sym/s |",
        "|---|" + "|".join("---" for _ in range(len(stage_names) + 3)) + "|",
    ]
    for r in records:
        cells = [
            f"{s['seconds']:.2f}" if s["seconds"] < 60 else f"{s['seconds'] / 60:.1f}m"
            for s in r["stages"]
        ]
        lines.append(
            f"| {r['universe_size']} | {r['total_seconds']:.1f} | "
            + " | ".join(cells) + f" | {r['poll_symbols_per_sec']} |"
        )
    write_text("throughput", "summary.md", "\n".join(lines) + "\n")
    print("wrote benchmarks/throughput/")


if __name__ == "__main__":
    main()
