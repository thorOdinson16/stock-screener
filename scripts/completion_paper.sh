#!/usr/bin/env bash
# Pair extension/current/momentum paper signals on one verified fresh snapshot.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
input="${1:?Pass a fresh research or live export directory}"
research="${COMPLETION_DIR:-$REPO_ROOT/ml/evaluation/results/remaining_plan_20261108/run}"
if [ ! -f "$research/report.json" ]; then
  log "Completion research has not finished; paired capture skipped"
  exit 0
fi
stage="reference_scoring"
failure() {
  local result=$?
  if [ "$result" -ne 0 ]; then
    python3 - "$research/paper/status.json" "$stage" <<'PY'
from pathlib import Path
from datetime import datetime, timezone
import sys,json
p=Path(sys.argv[1]); p.parent.mkdir(parents=True,exist_ok=True)
old=json.loads(p.read_text()) if p.exists() else {}
p.write_text(json.dumps({**old,"status":"error","failed_stage":sys.argv[2],"error_at":datetime.now(timezone.utc).isoformat(),"production_changed":False}))
PY
  fi
}
trap failure EXIT
reference="$research/paper/reference_$(date -u +%Y%m%dT%H%M%SZ).json"
options=()
if python3 - "$input/manifest.json" <<'PY'
import json,sys
raise SystemExit(0 if 'silver.quotes_enriched' in json.load(open(sys.argv[1]))['source_snapshots'] else 1)
PY
then
  options+=(--use-silver)
fi
spark_submit "$ICEBERG_PKG" "$REPO_ROOT/spark/jobs/paper_current_reference.py" --warehouse "$WAREHOUSE" \
  --input "$input" --output "$reference" "${options[@]}"
stage="paired_capture_and_evaluation"
"$REPO_ROOT/ml/.venv/bin/python" "$REPO_ROOT/ml/evaluation/completion_forward.py" \
  --input "$input" --reference "$reference" --research "$research"
