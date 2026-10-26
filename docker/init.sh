#!/usr/bin/env bash
#
# init.sh — one-shot setup run on every `docker compose up` (the `init` service).
#
# First start: setup.md §1.2–1.4 (Kafka topics, Iceberg tables, Druid
# supervisors). Later starts: only re-suspend the Druid supervisors, as
# start-stack.sh does after every restart. Each one-time step leaves a marker on
# the state volume so it is skipped next time.

source "$(dirname "${BASH_SOURCE[0]}")/../scripts/lib.sh"

MARKERS=/data/state/.init
mkdir -p "$MARKERS"

wait_for() {
  local name="$1" timeout="$2"; shift 2
  local waited=0
  until "$@" >/dev/null 2>&1; do
    sleep 3; waited=$((waited + 3))
    if [ "$waited" -ge "$timeout" ]; then
      fail "$name not ready after ${timeout}s"
      exit 1
    fi
  done
  ok "$name ready"
}

port_open() { timeout 2 bash -c "cat < /dev/null > /dev/tcp/127.0.0.1/$1"; }

wait_for "HDFS" 180 hdfs dfsadmin -safemode wait
wait_for "Kafka" 180 port_open 9092
wait_for "SeaTunnel" 180 port_open 5801
wait_for "Druid overlord" 300 curl -fsS "$DRUID_URL/druid/indexer/v1/supervisor"

if [ ! -f "$MARKERS/topics" ]; then
  log "Creating Kafka topics"
  "$REPO_ROOT/kafka/topics/create-topics.sh"
  touch "$MARKERS/topics"
fi

if [ ! -f "$MARKERS/tables" ]; then
  log "Creating Iceberg tables"
  hdfs dfs -mkdir -p /warehouse
  spark_submit "$ICEBERG_PKG" "$REPO_ROOT/docker/create_tables.py" --warehouse "$WAREHOUSE"
  touch "$MARKERS/tables"
fi

export DRUID_ROUTER="$DRUID_URL"
if [ ! -f "$MARKERS/supervisors" ]; then
  log "Registering Druid supervisors (left suspended)"
  "$REPO_ROOT/druid/ingestion/submit.sh"
  touch "$MARKERS/supervisors"
else
  python3 "$REPO_ROOT/druid/ingestion/supervisors.py" suspend
fi

if [ ! -f "$REPO_ROOT/ml/models/selected.json" ]; then
  log "No model yet. Load data and train once with:"
  log "  docker compose exec airflow docker/bootstrap-data.sh"
fi
ok "Init complete"
