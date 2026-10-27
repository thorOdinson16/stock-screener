#!/usr/bin/env bash
#
# ingest.sh — Kafka -> Iceberg bronze via SeaTunnel. Quotes and daily bars always;
# fundamentals and financial statements only on full runs.
#
# Env: FULL_RUN=1

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

run_seatunnel quotes-job.conf
run_seatunnel quotes-daily-job.conf

if [ "${FULL_RUN:-0}" = "1" ]; then
  run_seatunnel fundamentals-job.conf
  run_seatunnel financials-job.conf
else
  log "Skipping fundamentals ingest (quick run)"
fi

ok "Ingest complete"
