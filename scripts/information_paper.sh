#!/usr/bin/env bash
# Append observed information and capture a paired cohort without retraining.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
input="${1:?Pass a fresh research export}"
reference="${2:?Pass matching current-model reference scores}"
research="${INFORMATION_RESEARCH:-$REPO_ROOT/ml/evaluation/results/information_research_20261109}"
archive="${INFORMATION_ARCHIVE:-$REPO_ROOT/ml/evaluation/results/information_archive}"
if [ ! -f "$research/report.json" ]; then
  log "Information trial is not frozen; capture skipped"
  exit 0
fi
"$POLLER_PYTHON" "$REPO_ROOT/poller/research_information.py" --output "$archive" --history-symbols 0 --max-downloads 60
"$REPO_ROOT/ml/.venv/bin/python" "$REPO_ROOT/ml/evaluation/information_research.py" \
  --output "$research" --fresh "$input" --reference "$reference"
