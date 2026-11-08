"""Source-checked mathematical-signal diagnostics without ML dependencies."""

import hashlib
import json
from pathlib import Path
from .corporate_actions import load_events, fingerprint

ROOT = Path(__file__).resolve().parents[2]
DEFAULT = ROOT / "ml/evaluation/results/quant_equations_20261109"


def read_report(directory=None):
    directory = Path(directory) if directory else DEFAULT
    path = directory / "report.json"
    if not path.exists():
        return {"status": "running" if (directory / "protocol.json").exists() else "not_available",
                "results": {}, "assessment_checks": {}, "evidence_current": False, "stale_reasons": [], "production_changed": False}
    report = json.loads(path.read_text())
    protocol = report["protocol"]
    digest = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest() if Path(p).exists() else None
    reasons = []
    if json.loads((directory / "protocol.json").read_text()) != protocol:
        reasons.append("Protocol differs from completed report")
    for name, expected in protocol["source_hashes"].items():
        if digest(ROOT / "ml/evaluation" / name) != expected:
            reasons.append(f"Equation/evaluation source changed: {name}")
    inputs = Path(protocol["input_directory"])
    manifest = inputs / "manifest.json"
    if digest(manifest) != protocol["input_manifest"]:
        reasons.append("Historical snapshot manifest changed")
    elif manifest.exists():
        meta = json.loads(manifest.read_text())
        if meta["events_digest"] != fingerprint(load_events()):
            reasons.append("Corporate-action evidence changed")
        for name, expected in meta["files"].items():
            if digest(inputs / name) != expected:
                reasons.append(f"Historical data changed: {name}")
        for name, expected in meta["source_hashes"].items():
            if digest(ROOT / name) != expected:
                reasons.append(f"Historical feature definition changed: {name}")
    base = Path(protocol["base_directory"])
    if digest(base / "protocol.json") != protocol["base_protocol"]:
        reasons.append("Reference experiment changed")
    report["evidence_current"], report["stale_reasons"] = not reasons, reasons
    if reasons:
        report["status"] = "stale_evidence"
    for periods in report["results"].values():
        for result in periods.values():
            for strategy in result["selection_diagnostic"]["strategies"].values():
                strategy.pop("cycles", None)
    return report
