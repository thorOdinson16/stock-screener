#!/usr/bin/env bash
#
# run_once.sh — run the full on-demand pipeline locally (bypassing Airflow).
# Useful for debugging.
#
# Usage:
#   scripts/run_once.sh              # quick (quotes only)
#   scripts/run_once.sh --full       # quotes + fundamentals
#   scripts/run_once.sh --full --history
#   scripts/run_once.sh --limit 50

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# Always leave the Druid supervisors suspended, even if a step fails.
trap '"$SCRIPTS_DIR/druid_supervisors.sh" suspend >/dev/null 2>&1 || true' EXIT

export FULL_RUN=0
export PUBLISH_HISTORY=0
export UNIVERSE_LIMIT="${DEFAULT_UNIVERSE_LIMIT}"

while [ $# -gt 0 ]; do
  case "$1" in
    --full)    FULL_RUN=1 ;;
    --history) PUBLISH_HISTORY=1 ;;
    --limit)   UNIVERSE_LIMIT="$2"; shift ;;
    *) fail "Unknown option: $1"; exit 2 ;;
  esac
  shift
done

"$SCRIPTS_DIR/preflight.sh"
"$SCRIPTS_DIR/druid_supervisors.sh" resume
"$SCRIPTS_DIR/poll.sh"
"$SCRIPTS_DIR/ingest.sh"
"$SCRIPTS_DIR/indicators.sh"
"$SCRIPTS_DIR/score.sh"
"$SCRIPTS_DIR/serving.sh"
"$SCRIPTS_DIR/wait_druid.sh" "${WAIT_DRUID_TIMEOUT:-240}"
"$SCRIPTS_DIR/druid_supervisors.sh" wait
"$SCRIPTS_DIR/druid_supervisors.sh" suspend

ok "Pipeline run complete"
