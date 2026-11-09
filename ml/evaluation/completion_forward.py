"""Paired fresh paper cohorts: extension, frozen TCN, current model and momentum."""

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "ml/feature_engineering")]


def main():
    import numpy as np
    import pandas as pd
    import torch
    import joblib
    from transform import MODEL_FEATURES, TECHNICAL_FEATURES
    from ml.evaluation.model_tournament import checked_manifest, sha
    from ml.evaluation.research_context import add_context, CONTEXT_FEATURES
    from ml.evaluation.tournament_models import SequenceModel, sequences, make_tcn
    from ml.evaluation.tournament_forward import capture, assess, freshness
    from ml.evaluation.paper_portfolio import evaluate_book
    from ml.evaluation.compare_baselines import candidates
    from ml.evaluation.corporate_actions import load_events
    from ml.evaluation.retail import Policy
    from ml.evaluation.forward_tracking import atomic_json, IST
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--research", type=Path, default=ROOT / "ml/evaluation/results/remaining_plan_20261108/run")
    parser.add_argument("--base", type=Path, default=ROOT / "ml/evaluation/results/model_tournament_20261108")
    args = parser.parse_args()
    if not (args.research / "report.json").exists():
        raise RuntimeError("Completion experiment must finish before starting its paper trial")
    protocol = json.loads((args.research / "protocol.json").read_text())
    for name, expected in protocol["source_hashes"].items():
        if sha(ROOT / "ml/evaluation" / name) != expected:
            raise RuntimeError("Frozen extension source changed")
    if sha(args.base / "protocol.json") != protocol["base_protocol"]:
        raise RuntimeError("Base research protocol changed")
    base_protocol = json.loads((args.base / "protocol.json").read_text())
    for name, expected in base_protocol["source_hashes"].items():
        if sha(ROOT / "ml/evaluation" / name) != expected:
            raise RuntimeError("Frozen shared research source changed")
    checked_manifest(Path(base_protocol["input_directory"]))
    manifest = checked_manifest(args.input)
    reference = json.loads(args.reference.read_text())
    if reference["input_manifest"] != sha(args.input / "manifest.json") or reference["trade_date"] != manifest["last_session"]:
        raise RuntimeError("Current-model control does not match the fresh source snapshot")
    now = datetime.now(IST)
    paper = args.research / "paper"
    paper.mkdir(exist_ok=True)
    if not freshness(manifest["last_session"], now.date().isoformat()):
        atomic_json(paper / "status.json", {"status": "waiting_for_fresh_data", "last_source_session": manifest["last_session"]})
        return
    frame = pd.read_parquet(args.input / "dataset.parquet").replace([np.inf, -np.inf], np.nan)
    frame = frame[~frame.symbol.str.startswith("^")].dropna(subset=MODEL_FEATURES).sort_values(["symbol", "trade_date"]).reset_index(drop=True)
    raw = pd.read_parquet(args.input / "bars.parquet")
    bars = {(r.symbol, r.trade_date): {k: getattr(r, k) if pd.notna(getattr(r, k)) else None for k in ("open", "low", "close", "volume")} for r in raw.itertuples()}
    sessions = sorted(raw[raw.symbol == "^NSEI"].trade_date.unique())
    indices = {d: i for i, d in enumerate(sessions)}
    context = pd.read_parquet(args.input / "context.parquet")
    industries = frame[["symbol", "industry"]].drop_duplicates("symbol").set_index("symbol").industry.to_dict()
    statistics = {}
    for r in context.itertuples():
        i = indices.get(r.trade_date)
        current = bars.get((r.symbol, r.trade_date), {}).get("close")
        old = bars.get((r.symbol, sessions[i - 63]), {}).get("close") if i is not None and i >= 63 else None
        statistics[(r.symbol, r.trade_date)] = {"symbol": r.symbol, "trade_date": r.trade_date, "close": current,
                    "industry": industries.get(r.symbol, "Unknown"), "volume_avg_20d": r.volume_avg_20d,
                    "volatility_20d": r.volatility_20d, "momentum": current / old - 1 if current and old and min(current, old) > 0 else None}
    frame = add_context(frame, bars, sessions, statistics)
    signal = manifest["last_session"]
    latest = frame[frame.trade_date == signal].copy()
    def temporal(root, frozen, tensor, valid):
        file = root / frozen["model_file"]
        if sha(file) != frozen["model_sha256"]:
            raise RuntimeError("Frozen temporal weights changed")
        ids = latest.index[valid[latest.index]].to_numpy()
        saved = torch.load(file, map_location="cpu", weights_only=True)
        model = SequenceModel()
        model.model = make_tcn(tensor.shape[1])
        model.model.load_state_dict(saved["state_dict"])
        model.mean, model.scale = saved["mean"].numpy(), saved["scale"].numpy()
        return pd.Series(model.predict(tensor, ids), index=ids)
    base_frozen = json.loads((args.base / "paper_candidate.json").read_text())
    if sha(args.base / "protocol.json") != base_frozen["protocol_sha256"]:
        raise RuntimeError("Frozen base protocol changed")
    tensor, valid = sequences(frame, TECHNICAL_FEATURES, sessions)
    control = temporal(args.base, base_frozen, tensor, valid)
    del tensor
    latest["tcn_control"] = control.reindex(latest.index)
    candidate_path = args.research / "paper_candidate.json"
    frozen = json.loads(candidate_path.read_text()) if candidate_path.exists() else None
    if frozen:
        if frozen["protocol_sha256"] != sha(args.research / "protocol.json"):
            raise RuntimeError("Frozen extension protocol changed")
        file = args.research / frozen["model_file"]
        if sha(file) != frozen["model_sha256"]:
            raise RuntimeError("Frozen extension model changed")
        if frozen["sequence"]:
            sequence_frame = frame.copy()
            for c in CONTEXT_FEATURES:
                sequence_frame[c + "_missing"] = sequence_frame[c].isna().astype(float)
                sequence_frame[c] = sequence_frame[c].fillna(0.)
            tensor, valid = sequences(sequence_frame, frozen["sequence_columns"], sessions)
            if file.suffix == ".pt":
                scores = temporal(args.research, frozen, tensor, valid)
            else:
                ids = latest.index[valid[latest.index]].to_numpy()
                model = joblib.load(file)
                x = pd.DataFrame(tensor[ids].reshape(len(ids), -1), index=ids, columns=model.features)
                scores = pd.Series(model.predict(x), index=ids)
            latest["score"] = scores.reindex(latest.index)
            del tensor
        else:
            latest["score"] = joblib.load(file).predict(latest)
    else:
        latest["score"] = latest.tcn_control
    reference_scores = {r["symbol"]: r["score"] for r in reference["rows"]}
    latest["current_model"] = latest.symbol.map(reference_scores)
    latest = latest.dropna(subset=["score", "tcn_control", "current_model"])
    if not np.isfinite(latest[["score", "tcn_control", "current_model"]].to_numpy()).all():
        raise RuntimeError("Nonfinite fresh scores")
    extra = latest.set_index("symbol")[["tcn_control", "current_model"]].to_dict("index")
    pool = candidates(latest[["symbol", "score"]].to_dict("records"), statistics, signal, Policy(), load_events())
    for r in pool:
        r.update(extra[r["symbol"]])
    baskets = {name: sorted(pool, key=lambda r: (-r[field], r["symbol"]))[:20] for name, field in
               [("candidate", "score"), ("current_model", "current_model"), ("tcn_control", "tcn_control"), ("momentum", "momentum")]}
    baskets["equal_weight"] = sorted(pool, key=lambda r: r["symbol"])
    fields = {"candidate": "score", "current_model": "current_model", "tcn_control": "tcn_control", "momentum": "momentum", "equal_weight": None}
    selected = {name: [{**{k: r.get(k) for k in ("symbol", "momentum", "industry", "trade_date", "volume_avg_20d", "volatility_20d")},
                        "score": r[fields[name]] if fields[name] else None} for r in rows] for name, rows in baskets.items()}
    candidate_hash = frozen["model_sha256"] if frozen else base_frozen["model_sha256"]
    bundle = hashlib.sha256((candidate_hash + reference["model_sha256"]).encode()).hexdigest()
    status = capture(paper / "predictions", now.date().isoformat(), signal, bundle, selected,
                     {"candidate": frozen["candidate"] if frozen else "original_tcn_research_control", "candidate_sha256": candidate_hash,
                      "current_model_sha256": reference["model_sha256"], "input_manifest": sha(args.input / "manifest.json"),
                      "paired_pool_size": len(pool), "score_meaning": "Uncalibrated ranking outputs; live confidence remains withheld"})
    summaries = []
    for path in sorted((paper / "predictions").glob("*.json")):
        record = json.loads(path.read_text())
        outcome = assess(record, bars, sessions, load_events())
        # The all-eligible baseline remains fractional; its >10 positions do not
        # get silently truncated into a purported equal-weight retail portfolio.
        books = {}
        for name, rows in record["baskets"].items():
            if name == "equal_weight":
                continue
            receipt_path = paper / "entries" / f"{path.stem}_{name}.json"
            receipt = json.loads(receipt_path.read_text()) if receipt_path.exists() else None
            book = evaluate_book(record, rows, bars, sessions, load_events(), receipt)
            if receipt is None and book.get("entry_receipt") is not None:
                receipt_path.parent.mkdir(exist_ok=True)
                # Exclusive write: retries never resize a previously entered book.
                try:
                    with receipt_path.open("x") as fh:
                        json.dump(book["entry_receipt"], fh, allow_nan=False, indent=2)
                except FileExistsError:
                    book = evaluate_book(record, rows, bars, sessions, load_events(), json.loads(receipt_path.read_text()))
            books[name] = book
        atomic_json(paper / "outcomes" / path.name, {"equal_notional": outcome, "retail_books": books})
        summaries.append(outcome)
    atomic_json(paper / "status.json", {**status, "paired_pool_size": len(pool), "last_source_session": signal,
                "captured_cohorts": len(summaries), "completed_cohorts": sum(s["status"] == "complete" for s in summaries),
                "candidate": frozen["candidate"] if frozen else "original_tcn_research_control", "production_changed": False,
                "controls": ["current_model", "momentum", "tcn_control", "equal_weight"],
                "method": "Immutable paired cohorts plus independent ten-position ledgers; no overlapping-cohort compounding"})
    from ml.evaluation.prospective_assessment import update
    update(paper, sessions)
    print(json.dumps(status))


if __name__ == "__main__":
    main()
