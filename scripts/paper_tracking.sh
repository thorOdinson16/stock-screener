#!/usr/bin/env bash
#
# paper_tracking.sh — update the separate, frozen-candidate paper trial after a
# pipeline run has already served its snapshot.
#
# This is deliberately NOT fatal: the production snapshot is already served, and
# the trial records its own failures in <tournament>/paper/status.json. Skipped
# silently when no completed validation-qualified candidate exists (for example
# on a fresh checkout, where ml/evaluation/results/ is empty).
#
# Env: TOURNAMENT_DIR (default: the 2026-11-08 tournament)

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

tournament="${TOURNAMENT_DIR:-$REPO_ROOT/ml/evaluation/results/model_tournament_20261108}"
if [ ! -f "$tournament/paper_candidate.json" ]; then
  log "No frozen paper candidate; skipping paper tracking"
  exit 0
fi

log "Updating the separate frozen-candidate paper trial"
if ! bash "$REPO_ROOT/scripts/tournament_paper.sh"; then
  log "Paper tracking failed; the production snapshot is already served. See $tournament/paper/status.json."
fi
exit 0
