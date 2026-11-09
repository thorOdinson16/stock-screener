#!/usr/bin/env bash
# Fixed mathematical ranking signals; local frozen data, no production changes.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
"$REPO_ROOT/ml/.venv/bin/python" "$REPO_ROOT/ml/evaluation/quant_equations.py" \
  --input "${1:-$REPO_ROOT/ml/evaluation/results/tournament_inputs_20261108}" \
  --base "${2:-$REPO_ROOT/ml/evaluation/results/model_tournament_20261108}" \
  --output "${3:-$REPO_ROOT/ml/evaluation/results/quant_equations_20261109}"
