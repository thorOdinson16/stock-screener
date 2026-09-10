#!/usr/bin/env bash
#
# stop-stack.sh — stops services started by start-stack.sh, in reverse order.

set -uo pipefail

LOG_DIR="$HOME/stack-logs"
PID_DIR="$LOG_DIR/pids"

log()  { echo -e "\033[1;34m[stop-stack]\033[0m $*"; }
ok()   { echo -e "\033[1;32m[  ok  ]\033[0m $*"; }
warn() { echo -e "\033[1;33m[ warn ]\033[0m $*"; }

stop_by_pidfile() {
  local name=$1
  local pidfile="$PID_DIR/$name.pid"
  if [ -f "$pidfile" ]; then
    local pid
    pid=$(cat "$pidfile")
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null
      sleep 2
      kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null
      ok "Stopped $name (pid $pid)"
    else
      warn "$name pid $pid not running"
    fi
    rm -f "$pidfile"
  else
    warn "No pidfile for $name — skipping"
  fi
}

log "Stopping Airflow..."
stop_by_pidfile airflow

log "Stopping Druid..."
# start-single-server-small launches multiple child JVMs under one process group;
# its own script provides a stop helper — fall back to pidfile kill if absent.
if [ -x "$DRUID_HOME/bin/service" ]; then
  "$DRUID_HOME/bin/service" --down >> "$LOG_DIR/druid.log" 2>&1
  ok "Druid services stopped via bin/service --down"
else
  stop_by_pidfile druid
fi

log "Stopping SeaTunnel..."
if [ -x "$SEATUNNEL_HOME/bin/stop-seatunnel-cluster.sh" ]; then
  "$SEATUNNEL_HOME/bin/stop-seatunnel-cluster.sh" >> "$LOG_DIR/seatunnel.log" 2>&1
  ok "SeaTunnel stopped via stop-seatunnel-cluster.sh"
else
  warn "stop-seatunnel-cluster.sh not found — falling back to pidfile kill"
  stop_by_pidfile seatunnel
fi
rm -f "$PID_DIR/seatunnel.pid"

log "Stopping Kafka..."
"$KAFKA_HOME/bin/kafka-server-stop.sh" >> "$LOG_DIR/kafka.log" 2>&1
stop_by_pidfile kafka

log "Stopping Hive Metastore..."
stop_by_pidfile hive-metastore

log "Stopping HDFS..."
"$HADOOP_HOME/sbin/stop-dfs.sh" >> "$LOG_DIR/hdfs.log" 2>&1

echo
log "All stop commands issued. Verify with: jps"