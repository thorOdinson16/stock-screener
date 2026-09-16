"""
Experiment 6 — effect of Kafka partitions and Spark parallelism (roadmap §4).

Varies `SPARK_MASTER` and (optionally) the Kafka partition count of the topics,
re-running the pipeline for each configuration and recording stage wall times.
Emits benchmarks/scalability/*.json + summary.

Prereq: stack up. Altering partitions is destructive to a running consumer group,
so pass --alter-topics only when you intend to recreate/rebalance.

    python benchmarks/scalability.py --masters "local[4]" "local[8]" --limit 100
"""

import argparse
import os

from common import (
    REPO_ROOT,
    pipeline_env,
    run_cmd,
    run_pipeline,
    stage_seconds,
    write_json,
    write_text,
)

TOPICS = ["market.quotes", "market.quotes.daily", "market.scores", "market.screener"]


def set_partitions(env, topic, partitions) -> dict:
    kafka_bin = os.path.join(env.get("KAFKA_HOME", "/home/abhi/kafka"), "bin")
    return run_cmd([
        os.path.join(kafka_bin, "kafka-topics.sh"), "--alter",
        "--bootstrap-server", env.get("KAFKA_BOOTSTRAP", "localhost:9092"),
        "--topic", topic, "--partitions", str(partitions),
    ])


def main():
    parser = argparse.ArgumentParser(description="Scalability experiment")
    parser.add_argument("--masters", nargs="+", default=["local[4]", "local[8]"])
    parser.add_argument("--partitions", type=int, nargs="*", default=[],
                        help="Optional partition counts to sweep (recreates topic partitions)")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--alter-topics", action="store_true")
    args = parser.parse_args()

    env = pipeline_env()
    partition_options = args.partitions or [None]
    records = []

    for master in args.masters:
        for partitions in partition_options:
            if partitions and args.alter_topics:
                for topic in TOPICS:
                    set_partitions(env, topic, partitions)

            label = f"{master}_p{partitions}" if partitions else master
            print(f"[scalability] {label} ...")
            result = run_pipeline(
                full=args.full, limit=args.limit,
                extra_env={"SPARK_MASTER": master},
                log_dir=os.path.join(REPO_ROOT, "benchmarks", "scalability", "logs"),
            )
            record = {
                "spark_master": master,
                "kafka_partitions": partitions,
                "universe_limit": args.limit,
                "total_seconds": round(result["total_seconds"], 3),
                "stages": stage_seconds(result),
            }
            records.append(record)
            write_json("scalability", f"{label.replace('[', '').replace(']', '')}.json", record)
            print(f"  total={record['total_seconds']}s {record['stages']}")

    lines = ["# Scalability", "",
             "| spark master | partitions | total s | stages |",
             "|---|---|---|---|"]
    for r in records:
        lines.append(
            f"| {r['spark_master']} | {r['kafka_partitions']} | {r['total_seconds']} | "
            + ", ".join(f"{k}={v}" for k, v in r["stages"].items()) + " |"
        )
    write_text("scalability", "summary.md", "\n".join(lines) + "\n")
    print("wrote benchmarks/scalability/")


if __name__ == "__main__":
    main()
