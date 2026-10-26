#!/usr/bin/env bash
#
# entrypoint.sh <role> — starts one service of the platform image.
#
# All containers share one network namespace (docker-compose.yml), so every
# service listens on, and reaches the others at, localhost — exactly as on the
# native single-machine install. Roles mirror the steps of start-stack.sh.

set -euo pipefail

role="${1:-}"
shift || true

log() { echo -e "\033[1;34m[$role]\033[0m $*"; }

# Runtime state shared by airflow/api/init (see the symlinks in the Dockerfile).
if [ -d /data/state ]; then
  mkdir -p /data/state/ml/models /data/state/ml/evaluation/results \
           /data/state/monitoring/metrics /data/state/monitoring/quality
  [ -f /data/state/universe_cache.json ] \
    || cp "$REPO_ROOT/docker/universe_cache.seed.json" /data/state/universe_cache.json
fi

case "$role" in
  net)
    # Holds the shared network namespace (and the published ports).
    exec sleep infinity
    ;;

  namenode)
    if [ ! -d /data/hdfs/namenode/current ]; then
      log "Formatting a new HDFS namenode"
      hdfs namenode -format -nonInteractive -force
    fi
    export HADOOP_HEAPSIZE_MAX="${HDFS_HEAP:-1g}"
    exec hdfs namenode
    ;;

  datanode)
    export HADOOP_HEAPSIZE_MAX="${HDFS_HEAP:-1g}"
    exec hdfs datanode
    ;;

  kafka)
    # The repo's KRaft config, with the log dir moved onto the Docker volume.
    conf=/tmp/kafka-server.properties
    sed 's#^log.dirs=.*#log.dirs=/data/kafka#' "$REPO_ROOT/kafka/configs/server.properties" > "$conf"
    export JAVA_HOME=/opt/java17
    if [ ! -f /data/kafka/meta.properties ]; then
      log "Formatting KRaft storage"
      kafka-storage.sh format -t "$(kafka-storage.sh random-uuid)" -c "$conf"
    fi
    export KAFKA_HEAP_OPTS="-Xms${KAFKA_HEAP:-1g} -Xmx${KAFKA_HEAP:-1g}"
    exec kafka-server-start.sh "$conf"
    ;;

  seatunnel)
    # The stock config serves SeaTunnel's REST API on 8080, which is Airflow's
    # port; use 58080 as the native install does.
    sed -i 's/^\(\s*port:\s*\)8080\s*$/\158080/' "$SEATUNNEL_HOME/config/seatunnel.yaml"
    grep -q '^-Xmx' "$SEATUNNEL_HOME/config/jvm_options" \
      || echo "-Xmx${SEATUNNEL_HEAP:-2g}" >> "$SEATUNNEL_HOME/config/jvm_options"
    exec "$SEATUNNEL_HOME/bin/seatunnel-cluster.sh"
    ;;

  druid)
    # Native uses the single-server-small profile (sized for a 64 GB host);
    # here start-druid sizes the services to the memory given to it.
    export DRUID_JAVA_HOME=/opt/java11
    exec "$DRUID_HOME/bin/start-druid" -m "${DRUID_MEMORY:-6g}"
    ;;

  airflow)
    export AIRFLOW__CORE__DAGS_FOLDER="$REPO_ROOT/airflow/dags"
    export AIRFLOW__CORE__DAGS_ARE_PAUSED_AT_CREATION=False
    export AIRFLOW__CORE__LOAD_EXAMPLES=False
    export AIRFLOW__CORE__SIMPLE_AUTH_MANAGER_USERS="${AIRFLOW_USER}:admin"
    mkdir -p "$AIRFLOW_HOME"
    # Fixed credentials (native standalone generates a random password).
    printf '{"%s": "%s"}\n' "$AIRFLOW_USER" "$AIRFLOW_PASSWORD" \
      > "$AIRFLOW_HOME/simple_auth_manager_passwords.json.generated"
    airflow db migrate
    airflow pools set screening 1 "Screening pipeline (mutual exclusion)"
    exec airflow standalone
    ;;

  api)
    cd "$REPO_ROOT"
    PYTHONPATH="$REPO_ROOT" exec "$REPO_ROOT/api/.venv/bin/uvicorn" api.main:app \
      --host 0.0.0.0 --port 8000
    ;;

  init)
    exec "$REPO_ROOT/docker/init.sh"
    ;;

  *)
    # Anything else runs as a command (e.g. `docker compose exec airflow bash`).
    exec "$role" "$@"
    ;;
esac
