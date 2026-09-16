#!/usr/bin/env bash
#
# serving.sh — publish the latest snapshot (and optionally the daily history) to
# Kafka for Druid ingestion.
#
# Env: PUBLISH_HISTORY=1 (also publish price history)

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

log "Publishing screener snapshot"
spark_submit "$ICEBERG_PKG,$KAFKA_PKG" "$REPO_ROOT/spark/jobs/publish_screener.py" \
  --bootstrap-servers "$KAFKA_BOOTSTRAP"

if [ "${PUBLISH_HISTORY:-0}" = "1" ]; then
  # Publish only bars newer than what Druid already serves, so on-demand runs
  # append incrementally instead of re-sending the whole history.
  since=$(druid_sql "SELECT MAX(__time) AS t FROM price_history" 2>/dev/null \
    | python3 -c 'import sys,json;
try:
    t=json.load(sys.stdin)[0]["t"]
    print((t or "")[:10])
except Exception:
    print("")' 2>/dev/null)
  log "Publishing price history (since: ${since:-beginning})"
  history_args=(--bootstrap-servers "$KAFKA_BOOTSTRAP")
  [ -n "$since" ] && history_args+=(--since "$since")
  spark_submit "$ICEBERG_PKG,$KAFKA_PKG" "$REPO_ROOT/spark/jobs/publish_history.py" \
    "${history_args[@]}"
fi

ok "Serving publish complete"
