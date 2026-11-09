#!/usr/bin/env bash
#
# wait_druid.sh [timeout_seconds] — waits until Druid is serving a screener
# snapshot published in the last few minutes (segment handoff lag).
#
# Env: none

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

timeout="${1:-240}"
max_age=300
deadline=$(( $(date +%s) + timeout ))
last=""
expected_file="$REPO_ROOT/ml/evaluation/results/forward/expected.json"
expected=""
expected_count=""
if [ -f "$expected_file" ]; then
  expected=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["published_at"])' "$expected_file")
  expected_count=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["count"])' "$expected_file")
fi

while [ "$(date +%s)" -lt "$deadline" ]; do
  # Guard the command substitutions: a transient Druid/query failure must not
  # abort the wait (which is the whole point of this retry loop).
  last=$(druid_sql "SELECT MAX(__time) AS t FROM screener" 2>/dev/null \
    | python3 -c 'import sys,json;print(json.load(sys.stdin)[0]["t"])' 2>/dev/null || true)

  if [ -n "$last" ] && [ "$last" != "None" ]; then
    age=$(python3 - "$last" <<'PY' 2>/dev/null || true
import sys
from datetime import datetime, timezone
t = datetime.fromisoformat(sys.argv[1].replace("Z", "+00:00"))
print(int((datetime.now(timezone.utc) - t).total_seconds()))
PY
)
    if [ -n "$age" ] && [ "$age" -lt "$max_age" ]; then
      if [ -n "$expected" ]; then
        served_count=$(druid_sql "SELECT COUNT(*) AS n FROM screener WHERE __time = TIME_PARSE('$expected')" \
          | python3 -c 'import json,sys;print(json.load(sys.stdin)[0]["n"])' 2>/dev/null || true)
        if [ "$served_count" != "$expected_count" ]; then
          sleep 5
          continue
        fi
      fi
      ok "Druid is serving a fresh snapshot (${age}s old)"
      log "Recording published picks and updating forward results"
      spark_submit "$ICEBERG_PKG" "$REPO_ROOT/spark/jobs/track_forward.py" \
        --warehouse "$WAREHOUSE" --druid-url "$DRUID_URL"
      if [ -f "$REPO_ROOT/ml/evaluation/results/model_tournament_20261108/paper_candidate.json" ]; then
        log "Updating the separate frozen-candidate paper trial"
        if ! bash "$REPO_ROOT/scripts/tournament_paper.sh"; then
          log "Tournament paper tracking failed; the production snapshot is already served. Review the paper-stage error above."
        fi
      fi
      exit 0
    fi
  fi

  sleep 10
done

fail "Druid did not expose a fresh snapshot within ${timeout}s (last: ${last:-none})"
exit 1
