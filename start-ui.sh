#!/usr/bin/env bash
#
# start-ui.sh — starts the web dashboard: FastAPI backend (:8000) and the Vite
# React dev server (:5173). Requires the stack (Druid with ingested datasources)
# to be running for data to appear.
#
# Logs: ~/stack-logs/ui-*.log   PIDs: ~/stack-logs/pids/ui-*.pid

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$HOME/stack-logs"
PID_DIR="$LOG_DIR/pids"
mkdir -p "$LOG_DIR" "$PID_DIR"

log() { echo -e "\033[1;34m[start-ui]\033[0m $*"; }
ok()  { echo -e "\033[1;32m[  ok  ]\033[0m $*"; }
fail(){ echo -e "\033[1;31m[ fail ]\033[0m $*"; }

wait_for_port() {
  local port=$1 name=$2 timeout=${3:-60} waited=0
  until ss -tln 2>/dev/null | awk '{print $4}' | grep -q ":${port}\$"; do
    sleep 1; waited=$((waited + 1))
    if [ "$waited" -ge "$timeout" ]; then
      fail "$name did not open port $port within ${timeout}s"
      return 1
    fi
  done
  ok "$name listening on port $port (${waited}s)"
}

# ---- backend ---------------------------------------------------------------
if [ ! -x "$REPO_ROOT/api/.venv/bin/uvicorn" ]; then
  log "Creating api/.venv and installing requirements..."
  python3 -m venv "$REPO_ROOT/api/.venv"
  "$REPO_ROOT/api/.venv/bin/pip" install --quiet --upgrade pip
  "$REPO_ROOT/api/.venv/bin/pip" install --quiet -r "$REPO_ROOT/api/requirements.txt"
fi

log "Starting FastAPI backend on :8000..."
setsid env PYTHONPATH="$REPO_ROOT" "$REPO_ROOT/api/.venv/bin/uvicorn" api.main:app \
  --host 0.0.0.0 --port 8000 >> "$LOG_DIR/ui-api.log" 2>&1 < /dev/null &
echo $! > "$PID_DIR/ui-api.pid"
wait_for_port 8000 "FastAPI backend" 30 || exit 1

# ---- frontend --------------------------------------------------------------
if [ ! -d "$REPO_ROOT/frontend/node_modules" ]; then
  log "Installing frontend dependencies (npm install)..."
  (cd "$REPO_ROOT/frontend" && npm install --no-audit --no-fund)
fi

log "Starting Vite dev server on :5173..."
( cd "$REPO_ROOT/frontend" && \
  setsid ./node_modules/.bin/vite --host 0.0.0.0 --port 5173 >> "$LOG_DIR/ui-vite.log" 2>&1 < /dev/null & \
  echo $! > "$PID_DIR/ui-vite.pid" )
wait_for_port 5173 "Vite dev server" 40 || exit 1

echo
ok "Dashboard  http://localhost:5173"
ok "API docs   http://localhost:8000/docs"
echo
echo "  Logs: $LOG_DIR/ui-api.log, $LOG_DIR/ui-vite.log"
echo "  Stop with: ./stop-ui.sh"
