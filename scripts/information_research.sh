#!/usr/bin/env bash
# Build timestamped information evidence and freeze the separate research trial.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
archive="${INFORMATION_ARCHIVE:-$REPO_ROOT/ml/evaluation/results/information_archive}"
"$POLLER_PYTHON" "$REPO_ROOT/poller/research_information.py" --output "$archive" \
  --history-symbols "${INFORMATION_HISTORY_SYMBOLS:-60}" --max-downloads "${INFORMATION_MAX_DOWNLOADS:-420}"
args=(--archive "$archive" --output "${INFORMATION_RESEARCH:-$REPO_ROOT/ml/evaluation/results/information_research_20261109}")
if [ "$#" -ge 2 ]; then args+=(--fresh "$1" --reference "$2"); fi
"$REPO_ROOT/ml/.venv/bin/python" "$REPO_ROOT/ml/evaluation/information_research.py" "${args[@]}"
