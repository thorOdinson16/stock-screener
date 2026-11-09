#!/usr/bin/env bash
#
# poll.sh — one poll cycle. Every run fetches quotes and the daily bars added
# since each symbol’s latest ingested date; full runs also fetch fundamentals + annual
# financial statements.
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

# Derive progress from committed Iceberg data, independently for each symbol.
# A failed export aborts the run rather than silently downloading all history.
watermarks=$(mktemp "${TMPDIR:-/tmp}/daily-watermarks.XXXXXX")
references="$watermarks.references"
trap 'rm -f "$watermarks" "$watermarks.tmp" "$references" "$references.tmp"' EXIT
spark_submit "$ICEBERG_PKG" "$REPO_ROOT/spark/jobs/daily_history.py" watermarks \
  --warehouse "$WAREHOUSE" --output "$watermarks" --reference-output "$references"
bar_args=(--once --include-index --delay 0.1 --bootstrap-servers "$KAFKA_BOOTSTRAP"
  --watermarks "$watermarks" --overlap-days "$DAILY_BARS_LOOKBACK_DAYS"
  --references "$references"
  --period "$DAILY_BARS_BOOTSTRAP_PERIOD")
[ -n "$limit" ] && bar_args+=(--universe-limit "$limit")
log "Fetching missing daily bars per symbol (overlap: $DAILY_BARS_LOOKBACK_DAYS days)"
( cd "$REPO_ROOT/poller" && "$POLLER_PYTHON" backfill.py "${bar_args[@]}" )

if [ "${FULL_RUN:-0}" = "1" ]; then
  log "Polling annual financial statements"
  fin_args=(--once --bootstrap-servers "$KAFKA_BOOTSTRAP")
  [ -n "$limit" ] && fin_args+=(--universe-limit "$limit")
  ( cd "$REPO_ROOT/poller" && "$POLLER_PYTHON" financials.py "${fin_args[@]}" )
fi
ok "Poll complete"
