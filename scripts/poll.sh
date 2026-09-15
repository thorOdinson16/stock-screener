#!/usr/bin/env bash
#
# poll.sh — one poll cycle. Full runs fetch quotes + fundamentals; quick runs
# fetch quotes only.
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
ok "Poll complete"
