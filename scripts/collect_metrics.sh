#!/usr/bin/env bash
#
# collect_metrics.sh — snapshot operational metrics after a pipeline run
# (Kafka lag, Druid, HDFS, Airflow). See monitoring/collect_metrics.py.

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

log "Collecting operational metrics"
python3 "$REPO_ROOT/monitoring/collect_metrics.py"
ok "Metrics collected"

# Data-quality checks (duplicates, stale quotes, fundamental outliers) were
# documented as delivered but never ran. Advisory only: it needs pandas, which
# the system python may lack, and must never fail a run that already succeeded.
log "Running data-quality checks"
if python3 "$REPO_ROOT/monitoring/data_quality.py"; then
  ok "Data-quality report written"
else
  log "Data-quality checks did not run (see above); continuing"
fi
