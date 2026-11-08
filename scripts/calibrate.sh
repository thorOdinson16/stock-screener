#!/usr/bin/env bash
# Read-only outcome audit + time-separated calibration, using existing research.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

research="${1:-}"
output="${2:-$REPO_ROOT/ml/evaluation/results/forecast_lab}"
if [ -z "$research" ]; then
  research=$(python3 - "$REPO_ROOT" <<'PY'
from pathlib import Path
import sys
paths = list((Path(sys.argv[1]) / 'ml/evaluation/results').glob('retail_research*/report.json'))
print(max(paths, key=lambda p: p.stat().st_mtime).parent if paths else '')
PY
)
fi
if [ -z "$research" ] || [ ! -f "$research/config.json" ]; then
  fail "No completed walk-forward research found. Run ml/evaluation/retail_research.py first."
  exit 1
fi

log "Auditing fixed-policy outcomes from $research"
spark_submit "$ICEBERG_PKG" "$REPO_ROOT/ml/evaluation/forecast_outcomes.py" \
  --warehouse "$WAREHOUSE" --research "$research" --output "$output"
log "Fitting probabilities and return bands, then assessing later periods"
"$PYSPARK_PYTHON" "$REPO_ROOT/ml/evaluation/fit_forecasts.py" --input "$output"
if python3 -c 'import matplotlib' >/dev/null 2>&1; then
  python3 "$REPO_ROOT/ml/evaluation/plot_forecasts.py" "$output/calibration.json"
fi
ok "Research artifacts updated; production models and live probabilities are unchanged"
