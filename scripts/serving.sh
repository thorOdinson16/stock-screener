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
  log "Publishing price history"
  spark_submit "$ICEBERG_PKG,$KAFKA_PKG" "$REPO_ROOT/spark/jobs/publish_history.py" \
    --bootstrap-servers "$KAFKA_BOOTSTRAP"
fi

ok "Serving publish complete"
