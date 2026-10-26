# One "platform" image that mirrors the native Linux install: the same pinned
# Apache releases, Java 11 + 17, Python 3.12 (system) + Python 3.10 (Spark,
# Airflow), and the repo at /opt/screening. Every service container runs this
# image with a different role (see docker/entrypoint.sh). A separate target
# builds the dashboard into nginx.
#
# Build context is the repo root (see docker-compose.yml).

ARG APACHE_MIRROR=https://archive.apache.org/dist

# ---- downloads (separate stages so BuildKit fetches them in parallel) -------

FROM ubuntu:24.04 AS fetch
RUN apt-get -o Acquire::Retries=5 update \
    && apt-get -o Acquire::Retries=5 install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
# fetch <url> <dir>: download with retries (flaky DNS/mirrors), then unpack into <dir>.
RUN printf '%s\n' '#!/bin/sh' 'set -e' \
      'curl -fSL --retry 10 --retry-all-errors --retry-delay 5 -o /tmp/pkg "$1"' \
      'mkdir -p "$2" && tar -xzf /tmp/pkg --strip-components=1 -C "$2" && rm /tmp/pkg' \
      > /usr/local/bin/fetch && chmod +x /usr/local/bin/fetch
ARG APACHE_MIRROR
ENV APACHE_MIRROR=${APACHE_MIRROR}

FROM fetch AS hadoop
RUN fetch "$APACHE_MIRROR/hadoop/common/hadoop-3.3.6/hadoop-3.3.6.tar.gz" /opt/hadoop \
    && rm -rf /opt/hadoop/share/doc

FROM fetch AS spark
RUN fetch "$APACHE_MIRROR/spark/spark-3.3.4/spark-3.3.4-bin-hadoop3.tgz" /opt/spark

FROM fetch AS kafka
RUN fetch "$APACHE_MIRROR/kafka/3.9.2/kafka_2.12-3.9.2.tgz" /opt/kafka \
    && rm -rf /opt/kafka/site-docs

FROM fetch AS seatunnel
ARG MAVEN=https://repo1.maven.org/maven2
RUN fetch "$APACHE_MIRROR/seatunnel/2.3.13/apache-seatunnel-2.3.13-bin.tar.gz" /opt/seatunnel \
    && for c in kafka iceberg; do \
         curl -fSL --retry 10 --retry-all-errors --retry-delay 5 \
           -o "/opt/seatunnel/connectors/connector-$c-2.3.13.jar" \
           "$MAVEN/org/apache/seatunnel/connector-$c/2.3.13/connector-$c-2.3.13.jar"; \
       done

# Druid's bundled ZooKeeper admin server would take Airflow's port 8080 (same
# one-time fix as setup.md §1.5).
FROM fetch AS druid
RUN fetch "$APACHE_MIRROR/druid/31.0.1/apache-druid-31.0.1-bin.tar.gz" /opt/druid \
    && rm -rf /opt/druid/quickstart \
    && echo "admin.enableServer=false" >> /opt/druid/conf/zk/zoo.cfg

# ---- platform image ---------------------------------------------------------

FROM ubuntu:24.04 AS platform
ARG DEBIAN_FRONTEND=noninteractive
RUN echo 'Acquire::Retries "5";' > /etc/apt/apt.conf.d/80retries \
    && apt-get update && apt-get install -y --no-install-recommends \
        openjdk-11-jdk-headless openjdk-17-jre-headless \
        python3 python3-venv python3-pip \
        software-properties-common gpg-agent \
        perl curl ca-certificates procps iproute2 tini \
    && add-apt-repository -y ppa:deadsnakes/ppa \
    && apt-get update && apt-get install -y --no-install-recommends python3.10 python3.10-venv \
    && apt-get purge -y software-properties-common gpg-agent && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/* \
    && arch="$(dpkg --print-architecture)" \
    && ln -s "/usr/lib/jvm/java-11-openjdk-$arch" /opt/java11 \
    && ln -s "/usr/lib/jvm/java-17-openjdk-$arch" /opt/java17

COPY --from=hadoop    /opt/hadoop    /opt/hadoop
COPY --from=spark     /opt/spark     /opt/spark
COPY --from=kafka     /opt/kafka     /opt/kafka
COPY --from=seatunnel /opt/seatunnel /opt/seatunnel
COPY --from=druid     /opt/druid     /opt/druid

# Service homes. config/pipeline.env keeps its own defaults and only reads these
# when they are set, so the native setup is unaffected.
ENV JAVA_HOME=/opt/java11 \
    HADOOP_HOME=/opt/hadoop \
    HADOOP_CONF_DIR=/opt/hadoop/etc/hadoop \
    SPARK_HOME=/opt/spark \
    KAFKA_HOME=/opt/kafka \
    SEATUNNEL_HOME=/opt/seatunnel \
    DRUID_HOME=/opt/druid \
    AIRFLOW_HOME=/data/airflow \
    REPO_ROOT=/opt/screening
ENV PATH=/opt/spark/bin:/opt/hadoop/bin:/opt/kafka/bin:/opt/seatunnel/bin:/opt/java11/bin:$PATH

# Airflow 3.3.0 on Python 3.10 (as on the native machine), pinned by the
# official constraints file.
RUN python3.10 -m venv /opt/airflow-venv \
    && /opt/airflow-venv/bin/pip install --no-cache-dir \
         "apache-airflow==3.3.0" "apache-airflow-providers-standard" \
         --constraint "https://raw.githubusercontent.com/apache/airflow/constraints-3.3.0/constraints-3.10.txt" \
    && ln -s /opt/airflow-venv/bin/airflow /usr/local/bin/airflow

# Python environments at the repo-relative paths scripts/lib.sh and
# config/pipeline.env already expect. Installed before the repo is copied so
# code edits don't invalidate them.
COPY poller/requirements.txt /tmp/req/poller.txt
COPY api/requirements.txt    /tmp/req/api.txt
RUN python3.10 -m venv /opt/screening/spark/.venv \
    && /opt/screening/spark/.venv/bin/pip install --no-cache-dir \
         "numpy==1.26.4" "pandas==2.1.4" "pyarrow==14.0.2" \
    && python3 -m venv /opt/screening/poller/.venv \
    && /opt/screening/poller/.venv/bin/pip install --no-cache-dir -r /tmp/req/poller.txt \
    && python3 -m venv /opt/screening/api/.venv \
    && /opt/screening/api/.venv/bin/pip install --no-cache-dir -r /tmp/req/api.txt

# Pre-fetch the Spark packages (--packages) into the Ivy cache so pipeline runs
# don't depend on Maven at run time.
RUN echo 'from pyspark.sql import SparkSession; SparkSession.builder.getOrCreate().stop()' > /tmp/warm.py \
    && PYSPARK_PYTHON=/opt/screening/spark/.venv/bin/python spark-submit --master 'local[1]' \
         --packages org.apache.iceberg:iceberg-spark-runtime-3.3_2.12:1.8.1,org.apache.spark:spark-sql-kafka-0-10_2.12:3.3.4 \
         /tmp/warm.py \
    && rm /tmp/warm.py

COPY docker/conf/core-site.xml docker/conf/hdfs-site.xml /opt/hadoop/etc/hadoop/
COPY docker/conf/spark-defaults.conf /opt/spark/conf/

COPY . /opt/screening
# A Windows checkout made without .gitattributes may carry CRLF line endings,
# which break bash. Normalise the scripts the containers execute.
RUN find /opt/screening -path '*/.venv' -prune -o -name '*.sh' -print0 | xargs -0 sed -i 's/\r$//' \
    && chmod +x /opt/screening/docker/*.sh /opt/screening/scripts/*.sh \
       /opt/screening/druid/ingestion/submit.sh /opt/screening/kafka/topics/create-topics.sh \
    # Runtime output lives on the `state` volume; symlinks keep the repo paths
    # the code writes to (and that the api container reads from).
    && for d in ml/models ml/evaluation/results monitoring/metrics monitoring/quality; do \
         rm -rf "/opt/screening/$d" && ln -s "/data/state/$d" "/opt/screening/$d"; \
       done \
    && cp /opt/screening/poller/universe/universe_cache.json /opt/screening/docker/universe_cache.seed.json \
    && ln -sf /data/state/universe_cache.json /opt/screening/poller/universe/universe_cache.json

WORKDIR /opt/screening
LABEL org.opencontainers.image.source="https://github.com/thorOdinson16/stock-screener"
ENTRYPOINT ["/usr/bin/tini", "--", "/opt/screening/docker/entrypoint.sh"]

# ---- dashboard --------------------------------------------------------------

FROM node:20-alpine AS frontend-build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM nginx:1.27-alpine AS frontend
COPY docker/conf/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=frontend-build /app/dist /usr/share/nginx/html
LABEL org.opencontainers.image.source="https://github.com/thorOdinson16/stock-screener"
