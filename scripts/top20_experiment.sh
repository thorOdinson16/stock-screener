#!/usr/bin/env bash
# Historical research only; requires the existing isolated ML environment.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
"$REPO_ROOT/ml/.venv/bin/python" "$REPO_ROOT/ml/evaluation/top20_experiment.py" \
  --research "${1:-$REPO_ROOT/ml/evaluation/results/xgb_kalman_20261109}" \
  --output "${2:-$REPO_ROOT/ml/evaluation/results/top20_targets_20261109}"
