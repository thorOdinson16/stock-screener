"""Serve historical comparison evidence without Spark or numerical libraries."""

import hashlib
import json
from pathlib import Path
from .corporate_actions import load_events, fingerprint

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPORT = ROOT / "ml/evaluation/results/baseline_comparison_20261108/report.json"


def read_report(path=None):
    path = Path(path) if path is not None else DEFAULT_REPORT
    if not path.exists():
        return {"status": "not_available", "production_changed": False, "results": {},
                "limitations": [], "evidence_current": False, "stale_reasons": []}
    report = json.loads(path.read_text())
    config = report["config"]
    reasons = []
    if config.get("events_digest") != fingerprint(load_events()):
        reasons.append("Corporate-action evidence changed")
    for name, expected in config.get("source_hashes", {}).items():
        source = ROOT / "ml/evaluation" / name
        if not source.exists() or hashlib.sha256(source.read_bytes()).hexdigest() != expected:
            reasons.append(f"Research source changed: {name}")
    universe = ROOT / "poller/universe/universe_cache.json"
    if not universe.exists() or hashlib.sha256(universe.read_bytes()).hexdigest() != config.get("universe_sha256"):
        reasons.append("Universe metadata changed")
    research = Path(config.get("research_directory", ROOT / "ml/evaluation/results/retail_research_20261108"))
    for name, expected in config.get("prediction_files", {}).items():
        source = research / name
        if not source.exists() or hashlib.sha256(source.read_bytes()).hexdigest() != expected:
            reasons.append(f"Prediction evidence changed: {name}")
    source_config = research / "config.json"
    if not source_config.exists() or hashlib.sha256(source_config.read_bytes()).hexdigest() != config.get("research_config_sha256"):
        reasons.append("Prediction research configuration changed")
    report["evidence_current"] = not reasons
    report["stale_reasons"] = reasons
    if reasons:
        report["status"] = "stale_evidence"
    return report
