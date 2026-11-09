#!/usr/bin/env bash
# Research only: existing frozen historical snapshot and reference predictions.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
if [ ! -x "$REPO_ROOT/ml/.venv/bin/python" ]; then
  fail "Create ml/.venv and install ml/requirements-xgb.txt first"
  exit 1
fi
"$REPO_ROOT/ml/.venv/bin/python" "$REPO_ROOT/ml/evaluation/xgb_kalman.py" \
  --input "${1:-$REPO_ROOT/ml/evaluation/results/tournament_inputs_20261108}" \
  --base "${2:-$REPO_ROOT/ml/evaluation/results/model_tournament_20261108}" \
  --output "${3:-$REPO_ROOT/ml/evaluation/results/xgb_kalman_20261109}"
