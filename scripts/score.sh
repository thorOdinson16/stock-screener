#!/usr/bin/env bash
#
# score.sh — score the latest cross-section into gold + market.scores.

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

log "Scoring the latest cross-section"
spark_submit "$ICEBERG_PKG,$KAFKA_PKG" "$REPO_ROOT/spark/jobs/score_stocks.py" \
  --top-k "$TOP_K" --bootstrap-servers "$KAFKA_BOOTSTRAP"
ok "Scoring complete"
