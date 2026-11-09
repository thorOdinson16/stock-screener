#!/usr/bin/env bash
# Observe existing frozen cohorts without exporting data or scoring models.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
research="${INFORMATION_RESEARCH:-$REPO_ROOT/ml/evaluation/results/information_research_20261109}"
seed="${PAPER_OBSERVATION_SEED:-$REPO_ROOT/ml/evaluation/results/paper_refresh_20261108}"
if [ ! -d "$research/paper/predictions" ]; then
  log "No frozen information cohorts to value"
  exit 0
fi
"$POLLER_PYTHON" "$REPO_ROOT/poller/paper_observations.py" --research "$research" --seed "$seed"
