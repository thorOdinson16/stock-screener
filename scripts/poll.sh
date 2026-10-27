#!/usr/bin/env bash
#
# poll.sh — one poll cycle. Every run fetches quotes and the daily bars added
# since the last scored date; full runs also fetch fundamentals + annual
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

# Daily bars drive the indicators and scores, so bring the history up to date:
# re-fetch from a few days before the latest scored date (picking up the final
# close of any partial bar). Without a scored date yet, fetch a year.
latest=$(druid_sql "SELECT trade_date FROM screener GROUP BY trade_date ORDER BY trade_date DESC LIMIT 1" 2>/dev/null \
  | python3 -c 'import sys,json
try:
    print(json.load(sys.stdin)[0]["trade_date"][:10])
except Exception:
    print("")' 2>/dev/null || true)
bar_args=(--once --include-index --delay 0.1 --bootstrap-servers "$KAFKA_BOOTSTRAP")
[ -n "$limit" ] && bar_args+=(--universe-limit "$limit")
if [ -n "$latest" ]; then
  start=$(date -d "$latest - ${DAILY_BARS_LOOKBACK_DAYS:-10} days" +%F)
  log "Fetching daily bars since $start (latest scored date: $latest)"
  bar_args+=(--start "$start")
else
  log "No scored date in Druid yet — fetching a year of daily bars"
  bar_args+=(--period 1y)
fi
( cd "$REPO_ROOT/poller" && "$POLLER_PYTHON" backfill.py "${bar_args[@]}" )

if [ "${FULL_RUN:-0}" = "1" ]; then
  log "Polling annual financial statements"
  fin_args=(--once --bootstrap-servers "$KAFKA_BOOTSTRAP")
  [ -n "$limit" ] && fin_args+=(--universe-limit "$limit")
  ( cd "$REPO_ROOT/poller" && "$POLLER_PYTHON" financials.py "${fin_args[@]}" )
fi
ok "Poll complete"
