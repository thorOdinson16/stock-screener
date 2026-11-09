#!/usr/bin/env bash
# Historical research only; freezes protocol before fitting. No promotion.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
output="${1:-$REPO_ROOT/ml/evaluation/results/decision_experiment_20261109_v3}"
if [ ! -f "$output/source_audit/report.json" ]; then
  "$POLLER_PYTHON" "$REPO_ROOT/poller/expectations_sources.py" --output "$output/source_audit"
fi
"$REPO_ROOT/ml/.venv/bin/python" "$REPO_ROOT/ml/evaluation/decision_experiment.py" --output "$output"
