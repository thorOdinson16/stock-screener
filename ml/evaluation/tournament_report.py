"""Dependency-light tournament dashboard report with explicit evidence freshness."""

import hashlib
import json
from pathlib import Path
from .corporate_actions import fingerprint, load_events

ROOT = Path(__file__).resolve().parents[2]
DEFAULT = ROOT / "ml/evaluation/results/model_tournament_20261108"


def read_report(directory=None):
    directory = Path(directory) if directory is not None else DEFAULT
    if not (directory / "report.json").exists():
        progress = directory / "progress.json"
        return {"status": "running" if progress.exists() else "not_available", "results": {}, "candidates": {},
                "production_changed": False, "selected_on_validation": None, "limits": [],
                "evidence_current": False, "stale_reasons": [],
                "progress": json.loads(progress.read_text()) if progress.exists() else None}
    report = json.loads((directory / "report.json").read_text())
    protocol = report["protocol"]
    reasons = []
    digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    protocol_path = directory / "protocol.json"
    if not protocol_path.exists() or json.loads(protocol_path.read_text()) != protocol:
        reasons.append("Protocol differs from completed report")
    for name, expected in protocol["source_hashes"].items():
        path = ROOT / "ml/evaluation" / name
        if not path.exists() or digest(path) != expected:
            reasons.append(f"Research source changed: {name}")
    inputs = Path(protocol["input_directory"])
    manifest_path = inputs / "manifest.json"
    if not manifest_path.exists() or digest(manifest_path) != protocol["input_manifest"]:
        reasons.append("Input snapshot manifest changed")
    else:
        manifest = json.loads(manifest_path.read_text())
        if manifest["events_digest"] != fingerprint(load_events()):
            reasons.append("Corporate-action evidence changed")
        for name, expected in manifest.get("files", {}).items():
            path = inputs / name
            if not path.exists() or digest(path) != expected:
                reasons.append(f"Exported data changed: {name}")
        for name, expected in manifest["source_hashes"].items():
            path = ROOT / name
            if not path.exists() or digest(path) != expected:
                reasons.append(f"Feature source changed: {name}")
    reference = Path(protocol["reference_directory"])
    for name, expected in protocol["reference_files"].items():
        path = reference / name
        if not path.exists() or digest(path) != expected:
            reasons.append(f"Reference predictions changed: {name}")
    frozen_path = directory / "paper_candidate.json"
    if frozen_path.exists():
        frozen = json.loads(frozen_path.read_text())
        model_path = directory / frozen["model_file"]
        if not model_path.exists() or digest(model_path) != frozen["model_sha256"]:
            reasons.append("Frozen paper model changed")
    report["evidence_current"] = not reasons
    report["stale_reasons"] = reasons
    if reasons:
        report["status"] = "stale_evidence"
    paper = directory / "paper/status.json"
    if paper.exists():
        report["paper_tracking"] = json.loads(paper.read_text())
    selected = report.get("selected_on_validation")
    assessment_reasons = []
    if selected:
        later = report["results"][selected]["assessment"]
        for scenario in ("base", "double_costs", "entry_delayed"):
            model = later["retail"][scenario]
            baseline = later["matched_momentum_retail"][scenario]
            if model["status"] != "complete" or baseline["status"] != "complete":
                assessment_reasons.append(f"Incomplete later {scenario} comparison")
            elif model["net_return"] <= baseline["net_return"]:
                assessment_reasons.append(f"Frozen choice does not beat matched momentum under later {scenario}")
        base = later["retail"]["base"]
        if base["max_drawdown"] is None or base["max_drawdown"] < -.25 or base["closed_trades"] < 20:
            assessment_reasons.append("Later drawdown or trade-count check failed")
    else:
        assessment_reasons.append("No validation-qualified candidate")
    report["paper_candidate_assessment"] = {"supported": bool(selected) and not assessment_reasons, "reasons": assessment_reasons,
                                            "meaning": "Historical diagnostic only; no production promotion or calibrated probability claim"}
    # Daily curves/trades remain in detailed artifacts. Keep dashboard responses
    # compact and avoid presenting dependent stock cases as independent trials.
    for periods in report["results"].values():
        for item in periods.values():
            diagnostic = item["selection_diagnostic"]
            for value in diagnostic["strategies"].values():
                value.pop("cycles", None)
    return report
