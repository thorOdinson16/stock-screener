"""Compact, source-checked status for every remaining research-plan component."""

import hashlib
import json
from pathlib import Path
from .corporate_actions import fingerprint, load_events

ROOT = Path(__file__).resolve().parents[2]
DEFAULT = ROOT / "ml/evaluation/results/remaining_plan_20261108/run"


def read_report(directory=None):
    directory = Path(directory) if directory else DEFAULT
    report_path = directory / "report.json"
    if not report_path.exists():
        return {"status": "running" if (directory / "protocol.json").exists() else "not_available", "results": {},
                "production_changed": False, "evidence_current": False, "stale_reasons": [], "steps": [], "probability_calibration": {}}
    report = json.loads(report_path.read_text())
    protocol = report["protocol"]
    sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
    reasons = []
    for name, expected in protocol["source_hashes"].items():
        path = ROOT / "ml/evaluation" / name
        if not path.exists() or sha(path) != expected:
            reasons.append(f"Research source changed: {name}")
    financial_path = directory / "financial_audit.json"
    if not financial_path.exists() or sha(financial_path) != protocol["financial_audit"]:
        reasons.append("Verified financial audit changed")
        financial = {"accepted": [], "rejected": [], "symbols": 0}
    else:
        financial = json.loads(financial_path.read_text())
        source_root = directory.parent / "filings"
        if not (source_root / "downloads.json").exists() or sha(source_root / "downloads.json") != protocol["filing_download_index"]:
            reasons.append("Publication-time filing index changed")
        for record in financial["accepted"]:
            path = source_root / "xml" / record["source"].rsplit("/", 1)[-1]
            if not path.exists() or sha(path) != record["document_sha256"]:
                reasons.append("An original verified filing changed")
                break
    audit_path = directory / "completion_audit.json"
    audit = json.loads(audit_path.read_text()) if audit_path.exists() else None
    if audit:
        if audit["source_report_sha256"] != sha(report_path):
            reasons.append("Robustness audit refers to a different report")
        audit_protocol = json.loads((directory / "audit_protocol.json").read_text())
        if sha(ROOT / "ml/evaluation/completion_audit.py") != audit_protocol["source_sha256"]:
            reasons.append("Robustness audit source changed")
        inputs = Path(audit["data_paths"]["input"])
        base = Path(audit["data_paths"]["base"])
        if sha(base / "protocol.json") != protocol["base_protocol"]:
            reasons.append("Base experiment protocol changed")
        base_protocol = json.loads((base / "protocol.json").read_text())
        for name, expected in base_protocol["source_hashes"].items():
            if sha(ROOT / "ml/evaluation" / name) != expected:
                reasons.append(f"Shared research source changed: {name}")
        if sha(inputs / "manifest.json") != protocol["input_manifest"]:
            reasons.append("Historical input manifest changed")
        manifest = json.loads((inputs / "manifest.json").read_text())
        if manifest["events_digest"] != fingerprint(load_events()):
            reasons.append("Corporate-action evidence changed")
        for name, expected in manifest["files"].items():
            if not (inputs / name).exists() or sha(inputs / name) != expected:
                reasons.append(f"Historical input changed: {name}")
        for name, expected in manifest["source_hashes"].items():
            if sha(ROOT / name) != expected:
                reasons.append(f"Historical feature definition changed: {name}")
        report["robustness_checks"] = audit["checks"]
        report["verified_financial_later_only"] = audit["verified_financial_later_only"]
        report["ensemble"] = audit["ensemble"]
    paper_path = directory / "paper/status.json"
    paper = json.loads(paper_path.read_text()) if paper_path.exists() else {"status": "not_started", "captured_cohorts": 0, "completed_cohorts": 0}
    report["paper_tracking"] = paper
    prospective = directory / "paper/assessment.json"
    report["prospective_assessment"] = json.loads(prospective.read_text()) if prospective.exists() else None
    assessment_policy = directory / "paper/assessment_policy.json"
    if assessment_policy.exists():
        policy = json.loads(assessment_policy.read_text())
        if sha(ROOT / "ml/evaluation/prospective_assessment.py") != policy["source_sha256"]:
            reasons.append("Prospective assessment policy source changed")
    cohorts = []
    for path in sorted((directory / "paper/predictions").glob("*.json"), reverse=True)[:20]:
        record = json.loads(path.read_text())
        outcome_path = directory / "paper/outcomes" / path.name
        outcome = json.loads(outcome_path.read_text()) if outcome_path.exists() else {}
        cohorts.append({"publication_date": record["publication_date"], "signal_date": record["signal_date"],
                        "shared_pool": record["provenance"]["paired_pool_size"],
                        "candidate": record["provenance"]["candidate"],
                        "strategies": {name: {k: book.get(k) for k in ("status", "entry_date", "net_return", "mark_to_market_return")}
                                       for name, book in outcome.get("retail_books", {}).items()}})
    report["paper_cohorts"] = cohorts
    report["financial_sources"] = {"verified_filings": len(financial["accepted"]), "verified_symbols": financial["symbols"],
                "rejected_filings": len(financial["rejected"]), "source_examples": financial["accepted"][:5],
                "features_with_observations": {name: sum(r.get(name) is not None for r in financial["accepted"]) for name in
                      ["verified_profit_margin", "verified_revenue_yoy", "verified_profit_yoy"]}}
    report["steps"] = [
        {"name": "Versioned experiment", "status": "complete"},
        {"name": "Context and verified financial subset", "status": "complete_with_coverage_limits"},
        {"name": "Categorical, profit and outperformance models", "status": "complete"},
        {"name": "Contextual temporal model and equal-input control", "status": "complete"},
        {"name": "Retail, cost/delay and robustness tests", "status": "complete" if audit else "audit_pending"},
        {"name": "Conservative advancement checks", "status": "complete" if audit else "audit_pending"},
        {"name": "Fresh paired paper signals", "status": "collecting" if paper.get("captured_cohorts", 0) else paper["status"]},
        {"name": "Mature fresh 21-session evidence", "status": "pending_market_time" if not paper.get("completed_cohorts", 0) else "available_for_assessment"},
    ]
    frozen = directory / "paper_candidate.json"
    if frozen.exists():
        model = json.loads(frozen.read_text())
        if sha(directory / model["model_file"]) != model["model_sha256"]:
            reasons.append("Frozen extension weights changed")
    report["evidence_current"], report["stale_reasons"] = not reasons, reasons
    if reasons:
        report["status"] = "stale_evidence"
    # Retain evidence summaries; full cohorts/curves remain in local artifacts.
    for periods in report["results"].values():
        for result in periods.values():
            for strategy in result["selection_diagnostic"]["strategies"].values():
                strategy.pop("cycles", None)
    return report
