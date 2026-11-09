#!/usr/bin/env bash
# Read-only frozen forecast attribution. No service startup or retraining.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
"$REPO_ROOT/ml/.venv/bin/python" "$REPO_ROOT/ml/evaluation/diagnose_predictions.py" \
  --research "${1:-$REPO_ROOT/ml/evaluation/results/xgb_kalman_20261109}" \
  --output "${2:-$REPO_ROOT/ml/evaluation/results/prediction_diagnosis_20261109}"
