#!/usr/bin/env bash
#
# indicators.sh — recompute silver technical indicators (and fundamentals_clean on
# full runs).
#
# Env: FULL_RUN=1

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

extra=()
if [ "${FULL_RUN:-0}" != "1" ]; then
  extra+=(--skip-fundamentals)
fi

log "Computing silver indicators"
spark_submit "$ICEBERG_PKG" "$REPO_ROOT/spark/jobs/compute_indicators.py" "${extra[@]}"
ok "Indicators complete"
