#!/usr/bin/env bash
#
# submit.sh — submits all Druid Kafka ingestion supervisors (or updates them if
# they already exist). Druid must be running; the router/overlord listens on 8888.

set -uo pipefail

DRUID_ROUTER="${DRUID_ROUTER:-http://localhost:8888}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

for spec in "$SCRIPT_DIR"/*-kafka.json; do
  name=$(basename "$spec")
  echo "Submitting $name ..."
  curl -sS -X POST "$DRUID_ROUTER/druid/indexer/v1/supervisor" \
    -H 'Content-Type: application/json' \
    --data-binary "@$spec"
  echo
done

echo
echo "Suspending supervisors (the platform is on-demand; the pipeline resumes them per run):"
python3 "$SCRIPT_DIR/supervisors.py" suspend --router "$DRUID_ROUTER"

echo
echo "Current supervisors:"
curl -sS "$DRUID_ROUTER/druid/indexer/v1/supervisor"
echo
