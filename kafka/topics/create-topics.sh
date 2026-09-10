#!/usr/bin/env bash
#
# create-topics.sh — creates all Kafka topics for the stock screening platform.
# Run this once after the stack is up (needs Kafka broker listening on 9092).

set -euo pipefail

BOOTSTRAP="localhost:9092"
KAFKA_BIN="${KAFKA_HOME}/bin"

create_topic() {
  local name=$1 partitions=$2 replication=$3 retention_ms=$4
  echo "Creating topic: $name (partitions=$partitions, retention=${retention_ms}ms)"
  "$KAFKA_BIN/kafka-topics.sh" --create \
    --bootstrap-server "$BOOTSTRAP" \
    --topic "$name" \
    --partitions "$partitions" \
    --replication-factor "$replication" \
    --config "retention.ms=$retention_ms" \
    --if-not-exists
}

# name                    partitions  replication  retention
create_topic market.quotes        16  1            86400000     # 1 day
create_topic market.fundamentals   4  1            604800000    # 7 days (changes slowly, keep longer for debugging)
create_topic market.scores         4  1            604800000    # 7 days
create_topic market.deadletter     2  1            2592000000   # 30 days (want to keep failures around to inspect)

echo
echo "Topics created. Listing:"
"$KAFKA_BIN/kafka-topics.sh" --list --bootstrap-server "$BOOTSTRAP"