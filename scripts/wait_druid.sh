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

while [ "$(date +%s)" -lt "$deadline" ]; do
  last=$(druid_sql "SELECT MAX(__time) AS t FROM screener" 2>/dev/null \
    | python3 -c 'import sys,json;print(json.load(sys.stdin)[0]["t"])' 2>/dev/null)

  if [ -n "$last" ] && [ "$last" != "None" ]; then
    age=$(python3 - "$last" <<'PY' 2>/dev/null
import sys
from datetime import datetime, timezone
t = datetime.fromisoformat(sys.argv[1].replace("Z", "+00:00"))
print(int((datetime.now(timezone.utc) - t).total_seconds()))
PY
)
    if [ -n "$age" ] && [ "$age" -lt "$max_age" ]; then
      ok "Druid is serving a fresh snapshot (${age}s old)"
      exit 0
    fi
  fi

  sleep 10
done

fail "Druid did not expose a fresh snapshot within ${timeout}s (last: ${last:-none})"
exit 1
