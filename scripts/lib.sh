#!/usr/bin/env bash
#
# lib.sh — shared helpers for the on-demand pipeline step scripts.
# Sources config/pipeline.env (service homes, Spark packages, defaults).

set -euo pipefail

SCRIPTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPTS_DIR/.." && pwd)"
export REPO_ROOT

# shellcheck source=/dev/null
source "$REPO_ROOT/config/pipeline.env"

# Resolve the poller interpreter against REPO_ROOT, ignoring an inherited value
# that does not exist (a stale env can otherwise silently break the poll step).
if [ -z "${POLLER_PYTHON:-}" ] || [ ! -x "${POLLER_PYTHON:-}" ]; then
  export POLLER_PYTHON="$REPO_ROOT/poller/.venv/bin/python"
fi

log()  { echo -e "\033[1;34m[pipeline]\033[0m $*"; }
ok()   { echo -e "\033[1;32m[  ok  ]\033[0m $*"; }
fail() { echo -e "\033[1;31m[ fail ]\033[0m $*"; }

# spark_submit <packages> <script> [args...]
spark_submit() {
  local packages="$1"; shift
  "$SPARK_HOME/bin/spark-submit" \
    --master "$SPARK_MASTER" \
    --driver-memory "$SPARK_DRIVER_MEMORY" \
    --packages "$packages" \
    "$@"
}

# poller_env — environment for the poller scripts (run from the poller dir so
# the local `universe` package and sibling modules import correctly).
run_poller() {
  ( cd "$REPO_ROOT/poller" && "$POLLER_PYTHON" poller.py "$@" )
}

# run_seatunnel <config-file-name>
run_seatunnel() {
  local conf="$REPO_ROOT/seatunnel/configs/$1"
  log "SeaTunnel: $1"
  "$SEATUNNEL_HOME/bin/seatunnel.sh" --config "$conf"
}

# druid_sql <query> -> JSON on stdout
druid_sql() {
  curl -sS --max-time 20 -X POST "$DRUID_URL/druid/v2/sql" \
    -H 'Content-Type: application/json' \
    --data-binary "{\"query\":\"$1\"}"
}
