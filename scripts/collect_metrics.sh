#!/usr/bin/env bash
#
# collect_metrics.sh — snapshot operational metrics after a pipeline run
# (Kafka lag, Druid, HDFS, Airflow). See monitoring/collect_metrics.py.

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

log "Collecting operational metrics"
python3 "$REPO_ROOT/monitoring/collect_metrics.py"
ok "Metrics collected"
