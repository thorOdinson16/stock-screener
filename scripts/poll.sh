#!/usr/bin/env bash
#
# poll.sh — one poll cycle. Full runs fetch quotes + fundamentals + annual
# financial statements; quick runs fetch quotes only.
#
# Env: FULL_RUN=1 (quotes+fundamentals) | UNIVERSE_LIMIT=N

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

args=(--once --bootstrap-servers "$KAFKA_BOOTSTRAP")

if [ "${FULL_RUN:-0}" = "1" ]; then
  log "Polling quotes + fundamentals"
else
  log "Polling quotes only (quick)"
  args+=(--quotes-only)
fi

limit="${UNIVERSE_LIMIT:-$DEFAULT_UNIVERSE_LIMIT}"
if [ -n "$limit" ]; then
  args+=(--universe-limit "$limit")
  log "Universe limited to $limit symbols"
fi

run_poller "${args[@]}"

if [ "${FULL_RUN:-0}" = "1" ]; then
  log "Polling annual financial statements"
  fin_args=(--once --bootstrap-servers "$KAFKA_BOOTSTRAP")
  [ -n "$limit" ] && fin_args+=(--universe-limit "$limit")
  ( cd "$REPO_ROOT/poller" && "$POLLER_PYTHON" financials.py "${fin_args[@]}" )
fi
ok "Poll complete"
