#!/usr/bin/env bash
#
# preflight.sh — verifies the services the pipeline depends on are reachable.

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

port_open() {
  timeout 2 bash -c "cat < /dev/null > /dev/tcp/127.0.0.1/$1" 2>/dev/null
}

missing=0
check() {
  if port_open "$1"; then
    ok "$2 (:${1})"
  else
    fail "$2 (:${1}) not reachable"
    missing=1
  fi
}

log "Preflight checks"
check 9000 "HDFS NameNode"
check 9092 "Kafka broker"
check 8888 "Druid router"
check 5801 "SeaTunnel Zeta"

if [ ! -f "$REPO_ROOT/ml/models/selected.json" ]; then
  fail "No selected model ($REPO_ROOT/ml/models/selected.json) — run the Retrain action first."
  missing=1
fi

if [ "$missing" -ne 0 ]; then
  fail "Preflight failed. Is the stack up (./start-stack.sh)?"
  exit 1
fi

ok "Preflight passed"
