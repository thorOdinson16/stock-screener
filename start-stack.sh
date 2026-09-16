#!/usr/bin/env bash
#
# start-stack.sh — starts the full big-data stack in dependency order:
#   HDFS (NameNode + DataNode) -> Hive Metastore -> Kafka -> SeaTunnel -> Druid -> Airflow
#
# Logs for each service go to ~/stack-logs/<service>.log
# PIDs are tracked in ~/stack-logs/pids/ so stop-stack.sh can find them.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

LOG_DIR="$HOME/stack-logs"
PID_DIR="$LOG_DIR/pids"
mkdir -p "$LOG_DIR" "$PID_DIR"

# ---- helpers ----------------------------------------------------------

log()  { echo -e "\033[1;34m[start-stack]\033[0m $*"; }
ok()   { echo -e "\033[1;32m[  ok  ]\033[0m $*"; }
fail() { echo -e "\033[1;31m[ fail ]\033[0m $*"; }
warn() { echo -e "\033[1;33m[ warn ]\033[0m $*"; }

port_free() {
  ! ss -tln 2>/dev/null | awk '{print $4}' | grep -q ":${1}\$"
}

wait_for_port() {
  local port=$1 name=$2 timeout=${3:-60}
  local waited=0
  until ! port_free "$port"; do
    sleep 2; waited=$((waited + 2))
    if [ "$waited" -ge "$timeout" ]; then
      fail "$name did not open port $port within ${timeout}s"
      return 1
    fi
  done
  ok "$name is listening on port $port (${waited}s)"
}

save_pid() { echo "$1" > "$PID_DIR/$2.pid"; }

# ---- 1. HDFS ------------------------------------------------------------

log "Starting HDFS (NameNode + DataNode)..."
"$HADOOP_HOME/sbin/start-dfs.sh" >> "$LOG_DIR/hdfs.log" 2>&1
# start-dfs.sh backgrounds its own daemons; verify via jps instead of a pidfile
sleep 5
if jps | grep -q NameNode && jps | grep -q DataNode; then
  ok "HDFS NameNode + DataNode running"
else
  fail "HDFS did not come up cleanly — check $LOG_DIR/hdfs.log"
  exit 1
fi
wait_for_port 9870 "HDFS NameNode WebUI" 60 || exit 1

# ---- 2. Hive Metastore ---------------------------------------------------

log "Starting Hive Metastore..."
nohup "$HIVE_HOME/bin/hive" --service metastore >> "$LOG_DIR/hive-metastore.log" 2>&1 &
save_pid $! hive-metastore
wait_for_port 9083 "Hive Metastore" 60 || exit 1

# ---- 3. Kafka (KRaft, broker+controller combined) ------------------------

log "Starting Kafka..."
nohup "$KAFKA_HOME/bin/kafka-server-start.sh" "$KAFKA_HOME/config/server.properties" \
  >> "$LOG_DIR/kafka.log" 2>&1 &
save_pid $! kafka
wait_for_port 9092 "Kafka broker" 60 || exit 1

# ---- 4. SeaTunnel (Zeta engine cluster) -----------------------------------

log "Starting SeaTunnel Zeta cluster..."
nohup "$SEATUNNEL_HOME/bin/seatunnel-cluster.sh" >> "$LOG_DIR/seatunnel.log" 2>&1 &
save_pid $! seatunnel
wait_for_port 5801 "SeaTunnel Zeta" 60 || exit 1

# ---- 5. Druid (single-server-small) ---------------------------------------

log "Starting Druid (single-server-small)..."
nohup "$DRUID_HOME/bin/start-single-server-small" >> "$LOG_DIR/druid.log" 2>&1 &
save_pid $! druid
wait_for_port 8888 "Druid Router" 90 || exit 1

# The platform is on-demand: leave the Kafka supervisors suspended when idle.
if [ -f "$REPO_ROOT/druid/ingestion/supervisors.py" ]; then
  python3 "$REPO_ROOT/druid/ingestion/supervisors.py" suspend >/dev/null 2>&1 \
    && ok "Druid supervisors suspended (on-demand)" \
    || warn "Could not suspend Druid supervisors (register them with druid/ingestion/submit.sh)"
fi

# ---- 6. Airflow (standalone: webserver + scheduler + triggerer) -----------

log "Starting Airflow (standalone)..."
export AIRFLOW__CORE__DAGS_FOLDER="$REPO_ROOT/airflow/dags"
nohup airflow standalone >> "$LOG_DIR/airflow.log" 2>&1 &
save_pid $! airflow
wait_for_port 8080 "Airflow webserver" 90 || exit 1
airflow pools set screening 1 "Screening pipeline (mutual exclusion)" >/dev/null 2>&1 \
  && ok "Airflow pool 'screening' ready" \
  || warn "Could not create Airflow pool 'screening' (create it manually)"

# ---- summary ----------------------------------------------------------

echo
log "All services started."
cat <<EOF

  HDFS NameNode UI     http://localhost:9870
  Hive Metastore       thrift://localhost:9083
  Kafka broker         localhost:9092
  SeaTunnel Zeta REST  http://localhost:5801
  Druid console        http://localhost:8888
  Airflow UI           http://localhost:8080

  Logs:  $LOG_DIR/
  PIDs:  $PID_DIR/

  To stop everything, run: ./stop-stack.sh
EOF
