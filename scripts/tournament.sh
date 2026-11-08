#!/usr/bin/env bash
# Isolated CPU research; HDFS is required only when exporting a new snapshot.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
input="${1:-$REPO_ROOT/ml/evaluation/results/tournament_inputs_20261108}"
output="${2:-$REPO_ROOT/ml/evaluation/results/model_tournament_20261108}"
if [ ! -x "$REPO_ROOT/ml/.venv/bin/python" ]; then
  fail "Create ml/.venv and install ml/requirements.txt plus CPU torch first; see docs/model-tournament.md"
  exit 1
fi
if [ -f "$output/report.json" ] || [ -f "$output/paper_candidate.json" ]; then
  log "This run is frozen. Inspect its artifacts or choose a new output directory; models will not be refitted in place."
  exit 0
fi
if [ ! -f "$input/manifest.json" ]; then
  spark_submit "$ICEBERG_PKG" "$REPO_ROOT/ml/evaluation/export_tournament.py" --warehouse "$WAREHOUSE" --output "$input"
fi
"$REPO_ROOT/ml/.venv/bin/python" "$REPO_ROOT/ml/evaluation/model_tournament.py" --input "$input" --output "$output" --sequence
