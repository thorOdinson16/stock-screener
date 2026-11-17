#!/usr/bin/env bash
#
# submit.sh — submits all Druid Kafka ingestion supervisors (or updates them if
# they already exist). Druid must be running; the router/overlord listens on 8888.
#
# Exits non-zero if any submission is rejected, so callers (docker/init.sh) do
# not record a failed registration as done.

set -uo pipefail

DRUID_ROUTER="${DRUID_ROUTER:-http://localhost:8888}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

failed=0
for spec in "$SCRIPT_DIR"/*-kafka.json; do
  name=$(basename "$spec")
  echo "Submitting $name ..."
  # --fail turns an HTTP error (e.g. 400 for a bad spec) into a non-zero exit.
  if ! curl -sS --fail -X POST "$DRUID_ROUTER/druid/indexer/v1/supervisor" \
      -H 'Content-Type: application/json' \
      --data-binary "@$spec"; then
    echo "FAILED to submit $name" >&2
    failed=$((failed + 1))
  fi
  echo
done

if [ "$failed" -ne 0 ]; then
  echo "$failed supervisor spec(s) were not accepted by Druid." >&2
  exit 1
fi

echo
echo "Suspending supervisors (the platform is on-demand; the pipeline resumes them per run):"
python3 "$SCRIPT_DIR/supervisors.py" suspend --router "$DRUID_ROUTER" || exit 1

echo
echo "Current supervisors:"
curl -sS --fail "$DRUID_ROUTER/druid/indexer/v1/supervisor"
echo
