#!/usr/bin/env bash
#
# druid_supervisors.sh <suspend|resume|wait|status> — keep the Druid Kafka
# supervisors suspended at rest; resume/wait/suspend around an on-demand run.

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

export DRUID_ROUTER="${DRUID_ROUTER:-$DRUID_URL}"
python3 "$REPO_ROOT/druid/ingestion/supervisors.py" "$@"
