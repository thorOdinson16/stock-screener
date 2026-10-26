#!/usr/bin/env bash
#
# bootstrap-data.sh [--limit N] — first-time data load for a fresh Docker stack
# (setup.md §1.6–1.9): 5y daily backfill + index -> SeaTunnel -> silver
# indicators -> train/evaluate/select a model. Afterwards press **Run pipeline**
# (Full, with price history) in the dashboard.
#
# Run inside the airflow container:
#   docker compose exec airflow docker/bootstrap-data.sh            # full NIFTY 500
#   docker compose exec airflow docker/bootstrap-data.sh --limit 20 # smoke test

source "$(dirname "${BASH_SOURCE[0]}")/../scripts/lib.sh"

backfill_args=(--once --include-index --period 5y --bootstrap-servers "$KAFKA_BOOTSTRAP")
while [ $# -gt 0 ]; do
  case "$1" in
    --limit) backfill_args+=(--universe-limit "$2"); shift ;;
    *) fail "Unknown option: $1"; exit 2 ;;
  esac
  shift
done

log "Backfilling daily bars (5y)"
( cd "$REPO_ROOT/poller" && "$POLLER_PYTHON" backfill.py "${backfill_args[@]}" )

log "Ingesting daily bars into bronze"
run_seatunnel quotes-daily-job.conf

"$SCRIPTS_DIR/indicators.sh"
"$SCRIPTS_DIR/retrain.sh"

ok "Bootstrap complete — now press Run pipeline (Full + price history) in the dashboard"
