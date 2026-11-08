#!/usr/bin/env bash
# A separate paper path: never publishes the experimental model to Druid/Kafka.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
tournament="${TOURNAMENT_DIR:-$REPO_ROOT/ml/evaluation/results/model_tournament_20261108}"
paper_stage="initializing"
record_paper_failure() {
  local paper_exit=$?
  if [ "$paper_exit" -ne 0 ]; then
    python3 - "$tournament/paper/status.json" "$paper_stage" <<'PY'
from datetime import datetime, timezone
from pathlib import Path
import json, sys
path = Path(sys.argv[1])
previous = json.loads(path.read_text()) if path.exists() else {}
state = {**previous, "status": "error", "failed_stage": sys.argv[2],
         "last_error_at": datetime.now(timezone.utc).isoformat(), "production_changed": False}
path.parent.mkdir(parents=True, exist_ok=True)
temporary = path.with_suffix(".tmp")
temporary.write_text(json.dumps(state, indent=2))
temporary.replace(path)
PY
  fi
}
trap record_paper_failure EXIT
if [ ! -f "$tournament/paper_candidate.json" ] || [ ! -f "$tournament/report.json" ]; then
  log "No completed validation-qualified tournament paper candidate"
  exit 0
fi
if [ ! -x "$REPO_ROOT/ml/.venv/bin/python" ]; then
  fail "Tournament paper environment missing: ml/.venv"
  exit 1
fi
input="${1:-}"
if [ -z "$input" ]; then
  paper_stage="exporting_features"
  input="$tournament/paper/inputs/$(date -u +%Y%m%dT%H%M%SZ)"
  spark_submit "$ICEBERG_PKG" "$REPO_ROOT/spark/jobs/export_tournament_live.py" \
    --warehouse "$WAREHOUSE" --paper "$tournament/paper" --output "$input"
fi
paper_stage="capture_and_evaluate"
"$REPO_ROOT/ml/.venv/bin/python" "$REPO_ROOT/ml/evaluation/tournament_forward.py" --input "$input" --tournament "$tournament"
if [ -f "$REPO_ROOT/ml/evaluation/results/remaining_plan_20261108/run/report.json" ]; then
  paper_stage="paired_completion_tracking"
  bash "$REPO_ROOT/scripts/completion_paper.sh" "$input"
fi
