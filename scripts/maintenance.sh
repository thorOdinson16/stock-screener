#!/usr/bin/env bash
#
# maintenance.sh — Iceberg compaction, manifest rewrite and snapshot expiry.
#
# Env: SNAPSHOT_RETENTION_DAYS, DRY_RUN=1

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

args=(--warehouse "$WAREHOUSE")
[ -n "${SNAPSHOT_RETENTION_DAYS:-}" ] && args+=(--snapshot-retention-days "$SNAPSHOT_RETENTION_DAYS")
[ "${DRY_RUN:-0}" = "1" ] && args+=(--dry-run)

log "Running Iceberg maintenance (expire snapshots older than ${SNAPSHOT_RETENTION_DAYS}d)"
spark_submit "$ICEBERG_PKG" "$REPO_ROOT/spark/jobs/maintain_iceberg.py" "${args[@]}"
ok "Maintenance complete"
