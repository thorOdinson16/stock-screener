"""
selection_gate.py — decide whether a retrain may replace the published model.

evaluate.py used to overwrite ml/models/selected.json with whatever scored best,
even when that "best" had no edge at all. One click on "Retrain model" could
therefore swap a working model for a worthless one while the dashboard kept
publishing rankings. The gate is deliberately minimal: the new pick must show a
positive information coefficient out of sample. It is a floor, not a promotion
criterion (the research documents explain why a stronger edge is not claimed).

Pure Python so it can be unit-tested without Spark.
"""

import math

# IC must be strictly above this to replace an existing model.
MIN_IC = 0.0


def _number(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def label_failures(label, label_results, model_name, min_ic=MIN_IC):
    """Reasons the model chosen for `label` does not clear the floor (empty = ok).

    `label_results` is evaluate.py's results["labels"][label]. The walk-forward
    IC of the selected model is used when it was computed (it is what selection
    was based on); otherwise the fixed-test IC of the chosen model.
    """
    walk_forward = label_results.get("walk_forward") or {}
    if _number(walk_forward.get("ic_mean")) is not None and not walk_forward.get("error"):
        source, ic = "walk-forward", _number(walk_forward["ic_mean"])
    else:
        source = "fixed-test"
        ic = _number((label_results.get(model_name) or {}).get("ic_mean"))
    if ic is None:
        return [f"{label}: {model_name} has no measurable {source} IC"]
    if ic <= min_ic:
        return [f"{label}: {model_name} {source} IC {ic:.4f} is not above {min_ic}"]
    return []


def gate_failures(results, selected, min_ic=MIN_IC):
    """All reasons across labels. `results`/`selected` are evaluate.py's dicts."""
    failures = []
    for label, entry in selected.get("labels", {}).items():
        label_results = results.get("labels", {}).get(label, {})
        failures += label_failures(label, label_results, entry.get("model_name"), min_ic)
    return failures
