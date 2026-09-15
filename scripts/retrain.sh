#!/usr/bin/env bash
#
# retrain.sh — rebuild the training dataset, retrain all models, re-evaluate and
# refresh ml/models/selected.json.

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

log "Building training dataset"
spark_submit "$ICEBERG_PKG" "$REPO_ROOT/spark/jobs/build_training.py" --warehouse "$WAREHOUSE"

log "Training models"
spark_submit "$ICEBERG_PKG" "$REPO_ROOT/ml/training/train_model.py" --warehouse "$WAREHOUSE"

log "Evaluating + selecting"
spark_submit "$ICEBERG_PKG" "$REPO_ROOT/ml/evaluation/evaluate.py" --warehouse "$WAREHOUSE"

ok "Retrain complete"
