"""Fit and assess forecast calibration from the audited outcome CSV."""

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from calibration import DEFAULT_ROOT, build_artifact
from forward_tracking import atomic_json
from corporate_actions import load_events, fingerprint


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()
    audit = json.loads((args.input / "outcome_audit.json").read_text())
    if audit["events_digest"] != fingerprint(load_events()):
        raise RuntimeError("Corporate-action evidence changed; rerun the outcome audit")
    path = args.input / "outcomes.csv"
    if hashlib.sha256(path.read_bytes()).hexdigest() != audit["outcomes_sha256"]:
        raise RuntimeError("Outcome CSV does not match the audit manifest")
    with path.open() as fh:
        cases = list(csv.DictReader(fh))
    for r in cases:
        r["horizon"], r["fold"], r["rank"] = (int(r[k]) for k in ("horizon", "fold", "rank"))
        r["eligible"] = r["eligible"] == "True"
        for key in ("rank_percentile", "volatility", "net_return", "profit", "outperform"):
            r[key] = float(r[key]) if r.get(key) else None
    artifact = build_artifact(cases, audit)
    artifact["created_at"] = datetime.now(timezone.utc).isoformat()
    existing = args.input / "calibration.json"
    if existing.exists():
        previous = json.loads(existing.read_text())
        if previous.get("outcomes_sha256") != artifact["outcomes_sha256"] or previous.get("algorithm_sha256") != artifact["algorithm_sha256"]:
            atomic_json(args.input / "history" / (hashlib.sha256(existing.read_bytes()).hexdigest() + ".json"), previous)
    atomic_json(args.input / "calibration.json", artifact)
    lines = ["# Forecast calibration — research only", "", "Net-profit probabilities and 80% net-return bands were assessed on later date blocks. Production is unchanged.", "",
             "| Horizon | Fit cases | Later cases | Profit Brier | Constant baseline | Profit check | Band coverage | Band check |", "|---|---:|---:|---:|---:|---|---:|---|"]
    for h, item in artifact["horizons"].items():
        m, ranges = item["probability_metrics"]["profit"], item["range_metrics"]
        def num(v):
            return "—" if v is None else f"{v:.4f}"
        lines.append(f"| {h} | {item['fit_rows']} | {item['assessment_rows']} | {num(m.get('brier'))} | {num(m.get('baseline_brier'))} | {m['passed']} | {num(ranges.get('observed_coverage'))} | {ranges['passed']} |")
    for h, item in artifact["horizons"].items():
        m, ranges = item["probability_metrics"]["profit"], item["range_metrics"]
        lines += ["", f"## {h}-session reasons", ""]
        reasons = list(dict.fromkeys(m["reasons"] + ranges["reasons"]))
        lines += [f"- {reason}" for reason in reasons] or ["- Historical checks passed; source/prospective deployment checks remain outstanding."]
    lines += ["", "## Limits", ""] + [f"- {x}" for x in artifact["limits"]]
    (args.input / "summary.md").write_text("\n".join(lines) + "\n")
    print(f"CALIBRATION COMPLETE: {args.input / 'summary.md'}")


if __name__ == "__main__":
    main()
