#!/usr/bin/env bash
# Fixed pilot data collection and readiness audit. Never trains a model.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
"$REPO_ROOT/ml/.venv/bin/python" "$REPO_ROOT/ml/evaluation/pilot_coverage.py" --init
"$POLLER_PYTHON" "$REPO_ROOT/poller/pilot_filings.py" "$@"
"$REPO_ROOT/ml/.venv/bin/python" "$REPO_ROOT/ml/evaluation/pilot_coverage.py"
