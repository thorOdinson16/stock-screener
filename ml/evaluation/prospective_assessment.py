"""Prespecified fresh-cohort comparisons; overlapping returns never compound."""

import hashlib
import json
from pathlib import Path
import random

CONTROLS = ("current_model", "momentum", "tcn_control")
MINIMUM_CYCLES = 8


def summarize(cohorts, sessions):
    positions = {d: i for i, d in enumerate(sessions)}
    matured, pending = [], 0
    for record, outcome in sorted(cohorts, key=lambda pair: pair[0]["publication_date"]):
        future = [i for i, day in enumerate(sessions) if day > record["publication_date"]]
        horizon = record["policy"]["horizon"]
        if not future or future[0] + horizon >= len(sessions):
            pending += 1
            continue
        entry = future[0]
        matured.append({"publication_date": record["publication_date"], "entry": entry, "end": entry + horizon,
                        "books": outcome.get("retail_books", {})})
    # Select the schedule before checking whether returns are available. An
    # incomplete first cohort must not disappear in favor of a later winner.
    independent, previous_end = [], -1
    for cohort in matured:
        if cohort["entry"] > previous_end:
            independent.append(cohort)
            previous_end = cohort["end"]
    comparisons = {}
    for control in CONTROLS:
        differences = []
        complete = 0
        for cohort in matured:
            a, b = cohort["books"].get("candidate", {}), cohort["books"].get(control, {})
            complete += int(a.get("status") == b.get("status") == "complete" and a.get("net_return") is not None and b.get("net_return") is not None)
        for cohort in independent:
            a, b = cohort["books"].get("candidate", {}), cohort["books"].get(control, {})
            if a.get("status") == b.get("status") == "complete" and a.get("net_return") is not None and b.get("net_return") is not None:
                differences.append(a["net_return"] - b["net_return"])
        missing = 1 - complete / len(matured) if matured else None
        interval = None
        if len(differences) >= 2:
            rng = random.Random(42)
            draws = sorted(sum(rng.choice(differences) for _ in differences) / len(differences) for _ in range(2000))
            # Familywise 95% comparison across the three fixed controls.
            tail = .05 / (2 * len(CONTROLS))
            interval = [draws[int(tail * len(draws))], draws[min(len(draws) - 1, int((1 - tail) * len(draws)))]]
        reasons = []
        if len(differences) < MINIMUM_CYCLES:
            reasons.append("Fewer than eight completed non-overlapping fresh cohorts")
        if missing is None or missing > .02:
            reasons.append("Fresh matured pair coverage is unavailable or below 98%")
        if interval is None or interval[0] <= 0:
            reasons.append("A positive fresh advantage is not established")
        comparisons[control] = {"supported": not reasons, "reasons": reasons, "completed_independent_pairs": len(differences),
                                 "mean_net_return_difference": sum(differences) / len(differences) if differences else None,
                                 "familywise_95pct_interval": interval, "missing_matured_fraction": missing}
    return {"status": "awaiting_mature_cohorts" if not matured else "insufficient_evidence" if any(not c["supported"] for c in comparisons.values()) else "historical_paper_advantage_supported",
            "captured_cohorts": len(cohorts), "matured_cohorts": len(matured), "pending_cohorts": pending,
            "scheduled_independent_cohorts": len(independent), "comparisons": comparisons, "production_ready": False,
            "method": "Paired net returns on prescheduled non-overlapping cohort books; three fixed controls; 2000 bootstrap draws, seed 42; no overlapping-cohort compounding",
            "limits": ["Adjacent market regimes may remain dependent; intervals are descriptive.",
                       "Paper execution and provider/corporate-action limitations still apply.",
                       "The first 21-session result is not enough to establish a dependable edge. No automatic production promotion."]}


def update(directory, sessions):
    from .forward_tracking import atomic_json
    directory = Path(directory)
    policy = {"source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), "minimum_cycles": MINIMUM_CYCLES,
              "controls": list(CONTROLS), "minimum_coverage": .98, "familywise_alpha": .05,
              "schedule": "Greedy chronological non-overlap before checking outcome availability"}
    policy_path = directory / "assessment_policy.json"
    if policy_path.exists() and json.loads(policy_path.read_text()) != policy:
        raise RuntimeError("Prospective assessment policy changed; version the trial rather than retuning it")
    atomic_json(policy_path, policy)
    cohorts = []
    for path in sorted((directory / "predictions").glob("*.json")):
        outcome_path = directory / "outcomes" / path.name
        cohorts.append((json.loads(path.read_text()), json.loads(outcome_path.read_text()) if outcome_path.exists() else {}))
    result = summarize(cohorts, sessions)
    atomic_json(directory / "assessment.json", result)
    return result
