#!/usr/bin/env bash
# Read-only controlled comparison; HDFS must be running.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
spark_submit "$ICEBERG_PKG" "$REPO_ROOT/ml/evaluation/compare_baselines.py" \
  --warehouse "$WAREHOUSE" \
  --research "${1:-$REPO_ROOT/ml/evaluation/results/retail_research_20261108}" \
  --output "${2:-$REPO_ROOT/ml/evaluation/results/baseline_comparison_20261108}"
