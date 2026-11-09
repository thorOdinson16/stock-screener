#!/usr/bin/env bash
# Research only: audit gate first; no new live or prospective models.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
"$REPO_ROOT/ml/.venv/bin/python" "$REPO_ROOT/ml/evaluation/pilot_experiment.py" "$@"
