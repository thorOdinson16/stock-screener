"""Immutable prospective paper cohorts for a frozen tournament candidate.

Capture uses today's actual publication date, never a reconstructed historical
signal date. Cohorts are equal-notional diagnostics and are not compounded.
"""

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "ml/feature_engineering")]
from ml.evaluation.forward_tracking import atomic_json, IST
from ml.evaluation.corporate_actions import load_events
from ml.evaluation.retail import Policy


def freshness(signal_date, publication_date):
    from datetime import date
    age = (date.fromisoformat(publication_date) - date.fromisoformat(signal_date)).days
    return 0 <= age <= 5


def momentum_return(symbol, day, bars, sessions):
    """Use the same common-calendar 63-session baseline as the tournament."""
    from ml.evaluation.retail import number
    try:
        index = sessions.index(day)
    except ValueError:
        return None
    if index < 63:
        return None
    first = number(bars.get((symbol, sessions[index - 63]), {}).get("close"))
    last = number(bars.get((symbol, day), {}).get("close"))
    return last / first - 1 if first and last and min(first, last) > 0 else None


def capture(directory, publication_date, signal_date, model_hash, baskets, provenance):
    """Exclusive daily capture: retries cannot rewrite the prediction."""
    if not freshness(signal_date, publication_date):
        return {"status": "waiting_for_fresh_data", "last_source_session": signal_date}
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{publication_date}_{model_hash[:16]}.json"
    record = {"publication_date": publication_date, "signal_date": signal_date,
              "captured_at": datetime.now(IST).isoformat(), "model_sha256": model_hash,
              "baskets": baskets, "provenance": provenance, "policy": Policy().__dict__,
              "method": "Separate equal-notional stop/cost cohorts; no overlapping-cohort compounding"}
    # Serialize before opening exclusively so an encoding failure cannot leave
    # a corrupt immutable record. Atomic hard-link publishes the complete file.
    import os
    import tempfile
    payload = json.dumps(record, allow_nan=False, indent=2)
    fd, temporary = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(payload)
        try:
            os.link(temporary, path)
        except FileExistsError:
            return {"status": "already_captured", "path": str(path)}
    finally:
        os.unlink(temporary)
    return {"status": "captured", "path": str(path)}


def assess(record, bars, sessions, events):
    from ml.evaluation.forecast_outcomes import outcome
    future = [i for i, day in enumerate(sessions) if day > record["publication_date"]]
    if not future:
        return {"status": "pending", "reason": "No completed session after actual publication date", "baskets": {}}
    entry = future[0]
    result = {}
    for name, basket in record["baskets"].items():
        cases = [{"symbol": row["symbol"], **outcome(row["symbol"], entry, 21, bars, sessions, events, Policy(**record["policy"]))} for row in basket]
        statuses = {case["status"] for case in cases}
        status = "pending" if entry + 21 >= len(sessions) or "immature" in statuses else "complete" if statuses <= {"complete"} else "incomplete"
        result[name] = {"status": status, "cases": cases,
                        "net_return": (sum(case["net_return"] for case in cases) / len(cases) if cases else 0.) if status == "complete" else None}
    return {"status": "complete" if all(r["status"] == "complete" for r in result.values()) else "pending_or_incomplete",
            "publication_date": record["publication_date"], "signal_date": record["signal_date"], "baskets": result}


def main():
    import numpy as np
    import pandas as pd
    from transform import MODEL_FEATURES, TECHNICAL_FEATURES
    from ml.evaluation.model_tournament import checked_manifest, sha
    from ml.evaluation.tournament_models import predict, sequences, SequenceModel, make_tcn
    from ml.evaluation.compare_baselines import candidates
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="Fresh read-only export; never reconstruct past publication dates")
    parser.add_argument("--tournament", type=Path, default=ROOT / "ml/evaluation/results/model_tournament_20261108")
    args = parser.parse_args()
    paper = args.tournament / "paper"
    paper.mkdir(exist_ok=True)
    if not (args.tournament / "report.json").exists():
        atomic_json(paper / "status.json", {"status": "research_in_progress"})
        print("Historical assessment is not complete; no paper capture")
        return
    candidate_path = args.tournament / "paper_candidate.json"
    if not candidate_path.exists():
        atomic_json(paper / "status.json", {"status": "no_candidate_qualified"})
        print("No validation-qualified paper candidate")
        return
    frozen = json.loads(candidate_path.read_text())
    protocol_path = args.tournament / "protocol.json"
    if sha(protocol_path) != frozen["protocol_sha256"]:
        raise RuntimeError("Frozen protocol changed")
    protocol = json.loads(protocol_path.read_text())
    for name, expected in protocol["source_hashes"].items():
        if sha(ROOT / "ml/evaluation" / name) != expected:
            raise RuntimeError("Frozen model source changed; start a separately versioned paper trial")
    # The frozen candidate retains its original feature definitions and quality
    # policy. A revised ledger/transform requires a separately versioned trial.
    checked_manifest(Path(protocol["input_directory"]))
    manifest = checked_manifest(args.input)
    today = datetime.now(IST).date().isoformat()
    signal = manifest["last_session"]
    bar_frame = pd.read_parquet(args.input / "bars.parquet")
    bars = {(r.symbol, r.trade_date): {k: getattr(r, k) if pd.notna(getattr(r, k)) else None for k in ("open", "low", "close")} for r in bar_frame.itertuples()}
    sessions = sorted(bar_frame.loc[bar_frame.symbol == "^NSEI", "trade_date"].unique())
    # All captures are published at the current wall-clock date; the first
    # evaluated fill is strictly later. Stale prices cannot start a new cohort.
    if freshness(signal, today):
        frame = pd.read_parquet(args.input / "dataset.parquet")
        frame = frame[~frame.symbol.str.startswith("^")].replace([np.inf, -np.inf], np.nan).dropna(subset=MODEL_FEATURES).sort_values(["symbol", "trade_date"]).reset_index(drop=True)
        meta = frozen["metadata"]
        model_path = args.tournament / frozen["model_file"]
        if sha(model_path) != frozen["model_sha256"]:
            raise RuntimeError("Frozen model artifact changed")
        latest = frame[frame.trade_date == signal].copy()
        if meta["features"] == "sequence":
            tensor, valid = sequences(frame, TECHNICAL_FEATURES, sessions)
            latest = latest[valid[latest.index]]
            if meta["family"] == "tcn":
                import torch
                saved = torch.load(model_path, map_location="cpu", weights_only=True)
                model = SequenceModel()
                model.model = make_tcn(tensor.shape[1])
                model.model.load_state_dict(saved["state_dict"])
                model.mean, model.scale = saved["mean"].numpy(), saved["scale"].numpy()
                scores = model.predict(tensor, latest.index.to_numpy())
            else:
                import joblib
                flat = tensor[latest.index].reshape(len(latest), -1)
                scores = joblib.load(model_path).predict(flat)
        else:
            import joblib
            scores = predict(joblib.load(model_path), latest, protocol["features"][meta["features"]])
        if not np.isfinite(scores).all():
            raise RuntimeError("Frozen candidate produced nonfinite scores")
        latest["score"] = scores
        context = pd.read_parquet(args.input / "context.parquet")
        context = context[context.trade_date == signal]
        closes = latest.set_index("symbol").close.to_dict()
        industries = latest.set_index("symbol").industry.to_dict()
        stats = {(r.symbol, signal): {"symbol": r.symbol, "trade_date": signal, "close": closes.get(r.symbol),
                 "industry": industries.get(r.symbol, "Unknown"), "volume_avg_20d": r.volume_avg_20d,
                 "volatility_20d": r.volatility_20d, "momentum": momentum_return(r.symbol, signal, bars, sessions)} for r in context.itertuples()}
        pool = candidates(latest[["symbol", "score"]].to_dict("records"), stats, signal, Policy(), load_events())
        baskets = {"candidate": sorted(pool, key=lambda r: (-r["score"], r["symbol"]))[:20],
                   "momentum": sorted(pool, key=lambda r: (-r["momentum"], r["symbol"]))[:20],
                   "equal_weight": sorted(pool, key=lambda r: r["symbol"])}
        compact = {name: [{"symbol": r["symbol"], "score": r["score"], "momentum": r["momentum"]} for r in rows] for name, rows in baskets.items()}
        status = capture(paper / "predictions", today, signal, frozen["model_sha256"], compact,
                         {"input_manifest": sha(args.input / "manifest.json"), "candidate": frozen["candidate"], "events_digest": manifest["events_digest"]})
    else:
        status = {"status": "waiting_for_fresh_data", "last_source_session": signal, "asof": today}
    # Outcomes may update on stale exports, but captures never do. Previously
    # published predictions remain immutable and mature on the common calendar.
    evaluated = []
    for path in sorted((paper / "predictions").glob("*.json")):
        result = assess(json.loads(path.read_text()), bars, sessions, load_events())
        atomic_json(paper / "outcomes" / path.name, result)
        evaluated.append(result)
    atomic_json(paper / "status.json", {**status, "captured_cohorts": len(evaluated),
                 "completed_cohorts": sum(r["status"] == "complete" for r in evaluated),
                 "candidate": frozen["candidate"], "production_changed": False,
                 "method": "Immutable equal-notional paper cohorts; no overlapping-cohort compounding"})
    print(json.dumps(status))


if __name__ == "__main__":
    main()
