#!/usr/bin/env bash
#
# stop-ui.sh — stops the FastAPI backend and Vite dev server started by start-ui.sh.

set -uo pipefail

PID_DIR="$HOME/stack-logs/pids"

log()  { echo -e "\033[1;34m[stop-ui]\033[0m $*"; }
ok()   { echo -e "\033[1;32m[  ok  ]\033[0m $*"; }
warn() { echo -e "\033[1;33m[ warn ]\033[0m $*"; }

stop_by_pidfile() {
  local name=$1 pidfile="$PID_DIR/$1.pid"
  if [ -f "$pidfile" ]; then
    local pid; pid=$(cat "$pidfile")
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null
      sleep 2
      kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null
      ok "Stopped $name (pid $pid)"
    else
      warn "$name pid $pid not running"
    fi
    rm -f "$pidfile"
  else
    warn "No pidfile for $name — skipping"
  fi
}

log "Stopping dashboard..."
stop_by_pidfile ui-vite
stop_by_pidfile ui-api

# Fallback in case a service was started outside the pidfile flow.
pkill -f "node_modules/.bin/vite" 2>/dev/null && ok "Stopped stray vite process"
pkill -f "uvicorn api.main:app" 2>/dev/null && ok "Stopped stray uvicorn process"

echo
log "Done. Verify with: ss -tln | grep -E ':(8000|5173)'"
