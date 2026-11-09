#!/usr/bin/env bash
# Finish the frozen historical extensions; never promote a production model.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
input="${1:-$REPO_ROOT/ml/evaluation/results/tournament_inputs_20261108}"
base="${2:-$REPO_ROOT/ml/evaluation/results/model_tournament_20261108}"
output="${3:-$REPO_ROOT/ml/evaluation/results/remaining_plan_20261108/run}"
filings="${4:-$REPO_ROOT/ml/evaluation/results/remaining_plan_20261108/filings}"
"$REPO_ROOT/ml/.venv/bin/python" "$REPO_ROOT/ml/evaluation/completion_research.py" \
  --input "$input" --base "$base" --output "$output" --filings "$filings"
if [ ! -f "$output/completion_audit.json" ]; then
  "$REPO_ROOT/ml/.venv/bin/python" "$REPO_ROOT/ml/evaluation/completion_audit.py" --research "$output" --input "$input" --base "$base"
fi
ok "Research and robustness artifacts ready; fresh paper outcomes must mature in future sessions"
