"""Bounded architecture/target tournament with validation-only selection.

Historical research, not a new holdout. Outputs stay outside production models.
"""

import argparse
from dataclasses import asdict
from datetime import datetime
import hashlib
import importlib.metadata
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "ml/feature_engineering")]
import numpy as np
import pandas as pd
from transform import MODEL_FEATURES, TECHNICAL_FEATURES
from ml.evaluation.corporate_actions import load_events, fingerprint
from ml.evaluation.forecast_outcomes import outcome, feature_event_digest
from ml.evaluation.forward_tracking import atomic_json, IST
from ml.evaluation.retail import Policy, simulate
from ml.evaluation.compare_baselines import candidates, compare_period
from ml.evaluation.tournament_models import GRIDS, fit_model, predict, training_rows, rank_labels, date_weights, sequences, SequenceModel


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def checked_manifest(directory):
    manifest = json.loads((directory / "manifest.json").read_text())
    for name, expected in manifest["files"].items():
        if sha(directory / name) != expected:
            raise RuntimeError(f"Export changed: {name}")
    if manifest["events_digest"] != fingerprint(load_events()):
        raise RuntimeError("Export corporate evidence is stale")
    for name, expected in manifest["source_hashes"].items():
        if sha(ROOT / name) != expected:
            raise RuntimeError(f"Feature source changed: {name}; export a new snapshot")
    return manifest


def prepare(directory, output):
    manifest = checked_manifest(directory)
    frame = pd.read_parquet(directory / "dataset.parquet")
    frame = frame[~frame.symbol.str.startswith("^")].replace([np.inf, -np.inf], np.nan)
    frame = frame.dropna(subset=MODEL_FEATURES).sort_values(["symbol", "trade_date"]).reset_index(drop=True)
    bar_frame = pd.read_parquet(directory / "bars.parquet")
    bars = {(r.symbol, r.trade_date): {k: getattr(r, k) if pd.notna(getattr(r, k)) else None for k in ("open", "low", "close", "volume")}
            for r in bar_frame.itertuples()}
    sessions = sorted(bar_frame.loc[bar_frame.symbol == "^NSEI", "trade_date"].unique())
    positions = {day: i for i, day in enumerate(sessions)}
    events = load_events()
    target_file = output / "outcomes.parquet"
    target_manifest = {"input_manifest": sha(directory / "manifest.json"),
                       "algorithm": sha(ROOT / "ml/evaluation/forecast_outcomes.py"), "policy": asdict(Policy())}
    if target_file.exists():
        saved = json.loads((output / "outcomes_manifest.json").read_text())
        if saved["config"] != target_manifest or saved["sha256"] != sha(target_file):
            raise RuntimeError("Outcome checkpoint changed")
        targets = pd.read_parquet(target_file)
    else:
        rows = []
        for n, row in enumerate(frame.itertuples(), 1):
            i = positions.get(row.trade_date)
            result = outcome(row.symbol, i + 1, 21, bars, sessions, events, Policy()) if i is not None else {"status": "missing_calendar"}
            rows.append({"symbol": row.symbol, "trade_date": row.trade_date, **result})
            if n % 50000 == 0:
                print(f"TARGETS {n}/{len(frame)}", flush=True)
        targets = pd.DataFrame(rows)
        targets.to_parquet(target_file, index=False)
        atomic_json(output / "outcomes_manifest.json", {"config": target_manifest, "sha256": sha(target_file)})
    frame = frame.merge(targets, on=["symbol", "trade_date"], how="left", validate="one_to_one").reset_index(drop=True)
    frame["legacy_maturity"] = frame.trade_date.map(lambda d: sessions[positions[d] + 21] if d in positions and positions[d] + 21 < len(sessions) else None)
    context = pd.read_parquet(directory / "context.parquet")
    industries = frame[["symbol", "industry"]].drop_duplicates("symbol").set_index("symbol").industry.to_dict()
    closes = frame.set_index(["symbol", "trade_date"]).close.to_dict()
    statistics = {}
    for row in context.itertuples():
        key = (row.symbol, row.trade_date)
        position = positions.get(row.trade_date)
        previous = bars.get((row.symbol, sessions[position - 63]), {}).get("close") if position is not None and position >= 63 else None
        current = bars.get(key, {}).get("close")
        statistics[key] = {"symbol": row.symbol, "trade_date": row.trade_date,
                           "close": closes.get(key), "industry": industries.get(row.symbol, "Unknown"),
                           **{k: getattr(row, k) if pd.notna(getattr(row, k)) else None for k in
                              ("volume_avg_20d", "volatility_20d", "price_momentum_1m", "price_momentum_3m", "rsi_14")},
                           "momentum": current / previous - 1 if current and previous and min(current, previous) > 0 else None}
    return frame, bars, sessions, statistics, manifest


def quality(prediction, frame, target, through):
    joined = prediction.merge(frame[["symbol", "trade_date", "excess_ret_21d", "net_return", "legacy_maturity", "scheduled_exit_date"]],
                              on=["symbol", "trade_date"], how="left", validate="one_to_one")
    label = "excess_ret_21d" if target == "legacy" else "net_return"
    maturity = "legacy_maturity" if target == "legacy" else "scheduled_exit_date"
    complete = joined[label].notna() & joined[maturity].notna() & (joined[maturity] <= through)
    correlations = []
    for day, group in joined[complete].groupby("trade_date"):
        if len(group) >= 20 and group.score.nunique() > 1 and group[label].nunique() > 1:
            correlations.append((day, float(group.score.corr(group[label], method="spearman"))))
    finite = [value for _, value in correlations if np.isfinite(value)]
    return {"mean_daily_rank_ic": float(np.mean(finite)) if finite else None,
            "scored_dates": joined.trade_date.nunique(), "evaluated_dates": len(finite),
            "rows": len(joined), "known_target_rows": int(complete.sum())}


def signals_for(prediction, statistics, events, momentum=False):
    signals = {}
    for day, group in prediction.groupby("trade_date", sort=True):
        pool = candidates(group.to_dict("records"), statistics, day, Policy(), events)
        field = "momentum" if momentum else "score"
        selected = sorted(pool, key=lambda r: (-r[field], r["symbol"]))[:20]
        signals[day] = [{**r, "rank": i} for i, r in enumerate(selected, 1)]
    return signals


def retail_summary(result):
    return {k: result[k] for k in ("status", "blocked", "net_return", "max_drawdown", "trade_win_rate")} | {"closed_trades": len(result["trades"])}


def evaluate(prediction, statistics, bars, sessions, events, start, end, full_path=None):
    strategies = {"candidate": signals_for(prediction, statistics, events),
                  "matched_momentum": signals_for(prediction, statistics, events, momentum=True)}
    summaries, detailed = {}, {}
    for strategy, signals in strategies.items():
        summaries[strategy], detailed[strategy] = {}, {}
        for name, policy, lag in [("base", Policy(), 0), ("double_costs", Policy(fee_bps=20, slippage_bps=20), 0), ("entry_delayed", Policy(), 1)]:
            shifted = signals
            if lag:
                index = {d: i for i, d in enumerate(sessions)}
                shifted = {sessions[index[d] + lag]: rows for d, rows in signals.items() if index[d] + lag < len(sessions)}
            result = simulate(shifted, bars, sessions, policy, events, start, end)
            summaries[strategy][name] = retail_summary(result)
            detailed[strategy][name] = result
    # Same independent-cycle diagnostic as the baseline experiment, alongside
    # the actual ten-position daily retail allocator above.
    plain = {d: group.to_dict("records") for d, group in prediction.groupby("trade_date")}
    basket = compare_period(plain, statistics, bars, sessions, Policy(max_positions=1000), events, start, end)
    model_basket = basket["strategies"]["model"]
    selection = basket["selection_diagnostic"]
    if full_path:
        atomic_json(full_path, {"retail": detailed, "baskets": basket})
    return {"retail": summaries["candidate"], "matched_momentum_retail": summaries["matched_momentum"],
            "signal_dates": prediction.trade_date.nunique(), "scored_rows": len(prediction),
            "equal_target_basket": {k: model_basket[k] for k in ("status", "blocked", "net_return", "max_drawdown", "mean_entry_exposure", "closed_trades")},
            "selection_diagnostic": selection,
            "paired_basket_comparisons": basket["paired_comparisons"]}


def choose_paper_candidate(validation, metadata):
    """Only validation inputs enter the decision; unverified fundamentals excluded."""
    qualified, rejection = [], {}
    for name, report in validation.items():
        if name not in metadata:
            continue
        reasons = []
        meta = metadata[name]
        if meta["features"] == "legacy_all":
            reasons.append("Unverified annual publication dates")
        if meta["target"] != "execution":
            reasons.append("Not execution-aligned")
        if sum(q["mean_daily_rank_ic"] is not None and q["mean_daily_rank_ic"] > 0 for q in meta["fold_quality"]) < 2:
            reasons.append("Positive rank IC in fewer than two of three validation folds")
        for scenario in ("base", "double_costs", "entry_delayed"):
            model, baseline = report["retail"][scenario], report["matched_momentum_retail"][scenario]
            if model["status"] != "complete" or baseline["status"] != "complete":
                reasons.append(f"Incomplete {scenario} portfolio or momentum baseline")
            elif model["net_return"] <= baseline["net_return"]:
                reasons.append(f"Does not beat momentum under {scenario}")
        base = report["retail"]["base"]
        if base["closed_trades"] < 20:
            reasons.append("Fewer than twenty closed validation trades")
        if base["max_drawdown"] is None or base["max_drawdown"] < -.25:
            reasons.append("Validation drawdown unavailable or below -25%")
        rejection[name] = reasons
        if not reasons:
            qualified.append(name)
    winner = max(qualified, key=lambda n: (validation[n]["retail"]["base"]["net_return"], n)) if qualified else None
    return winner, rejection


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "ml/evaluation/results/tournament_inputs_20261108")
    parser.add_argument("--research", type=Path, default=ROOT / "ml/evaluation/results/retail_research_20261108")
    parser.add_argument("--output", type=Path, default=ROOT / "ml/evaluation/results/model_tournament_20261108")
    parser.add_argument("--sequence", action="store_true", help="Also run fixed TCN and equal-input flattened LightGBM control")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    inputs = checked_manifest(args.input)
    events = load_events()
    old_config = json.loads((args.research / "config.json").read_text())
    if old_config["source_snapshots"] != inputs["source_snapshots"]:
        raise RuntimeError("Current-reference price/statements snapshots do not match")
    old_events = json.loads((args.research / "source_snapshots/config/corporate_actions.json").read_text())["events"]
    if feature_event_digest(old_events) != feature_event_digest(events):
        raise RuntimeError("Current-reference feature windows differ")
    sources = ["model_tournament.py", "tournament_models.py", "compare_baselines.py", "retail.py", "forecast_outcomes.py", "corporate_actions.py"]
    protocol = {"input_directory": str(args.input.resolve()), "input_manifest": sha(args.input / "manifest.json"),
                "reference_directory": str(args.research.resolve()),
                "reference_files": {f"rank21_fold{i}.csv": sha(args.research / f"rank21_fold{i}.csv") for i in range(1, 6)},
                "source_hashes": {name: sha(ROOT / "ml/evaluation" / name) for name in sources},
                "versions": {name: importlib.metadata.version(name) for name in ("numpy", "pandas", "scikit-learn", "lightgbm", "catboost")},
                "grids": GRIDS, "features": {"price_only": TECHNICAL_FEATURES, "legacy_all": MODEL_FEATURES},
                "targets": ["legacy", "execution"], "validation_folds": [1, 2, 3], "assessment_folds": [4, 5],
                "embargo_sessions": 63, "seed": 42, "retail_policy": asdict(Policy()), "sequence": args.sequence,
                "evaluation_revision": "Matched inference-pool/date momentum controls; exact common-calendar momentum lookback",
                "sequence_protocol": {"lookback": 20, "channels": TECHNICAL_FEATURES, "epochs": 3,
                                      "hidden": 16, "dilations": [1, 2, 4, 8], "learning_rate": .001,
                                      "flattened_control": {"num_leaves": 7, "n_estimators": 100}},
                "config_selection": "Highest date-weighted mean rank IC in folds 1-3; lower grid index breaks ties. No assessment early stopping.",
                "paper_selection": "Price-only execution target, positive IC in at least two validation folds; >=20 trades; DD>=-25%; beats momentum on the candidate's identical inference pool/dates in base, double-cost and delayed-entry ten-position portfolios. Highest validation base return breaks ties. No automatic production promotion."}
    if args.sequence:
        protocol["versions"]["torch"] = importlib.metadata.version("torch")
    protocol_path = args.output / "protocol.json"
    if protocol_path.exists() and json.loads(protocol_path.read_text()) != json.loads(json.dumps(protocol)):
        raise RuntimeError("Tournament protocol differs; choose a new output directory")
    atomic_json(protocol_path, protocol)
    print("TOURNAMENT protocol locked before model fitting", flush=True)
    frame, bars, sessions, statistics, _ = prepare(args.input, args.output)
    references, folds = [], {}
    for fold in range(1, 6):
        reference = pd.read_csv(args.research / f"rank21_fold{fold}.csv", dtype={"trade_date": str})
        reference["fold"] = fold
        references.append(reference)
        days = sorted(reference.trade_date.unique())
        train_dates = sessions[:sessions.index(days[0]) - 63]
        folds[fold] = {"test_dates": days, "train_dates": train_dates}
    reference = pd.concat(references, ignore_index=True)
    common_keys = reference[["symbol", "trade_date"]].merge(frame[["symbol", "trade_date"]], validate="one_to_one")
    if len(common_keys) != len(reference):
        raise RuntimeError("Recomputed features differ from the reference scoring universe")
    periods = {"validation": (folds[1]["test_dates"][0], folds[3]["test_dates"][-1]),
               "assessment": (folds[4]["test_dates"][0], sessions[-1])}
    metadata, predictions = {}, {}
    checkpoints = args.output / "predictions"
    checkpoints.mkdir(exist_ok=True)
    def fit_fold(name, family, params, features, target, fold, subset=None, tensor=None):
        path = checkpoints / f"{name}_fold{fold}.parquet"
        receipt = path.with_suffix(".json")
        if path.exists() and receipt.exists():
            if json.loads(receipt.read_text())["sha256"] != sha(path):
                raise RuntimeError(f"Prediction checkpoint changed: {path.name}")
            return pd.read_parquet(path)
        spec = folds[fold]
        pool = frame if subset is None else frame.loc[subset]
        train = training_rows(pool, target, spec["train_dates"], spec["test_dates"][0])
        test_keys = references[fold - 1][["symbol", "trade_date"]]
        test = pool[pool.trade_date.isin(spec["test_dates"])]
        test = test.reset_index(names="row_index").merge(test_keys, on=["symbol", "trade_date"], validate="one_to_one").set_index("row_index")
        if train.empty or test.empty:
            raise RuntimeError(f"No common training/test samples: {name} fold {fold}")
        print(f"FIT {name} fold {fold}: {len(train)} train / {len(test)} test", flush=True)
        started = time.monotonic()
        if family == "tcn":
            model = SequenceModel().fit(tensor, train.index.to_numpy(), rank_labels(train, target), date_weights(train))
            scores = model.predict(tensor, test.index.to_numpy())
        else:
            model = fit_model(family, params, train, features, target)
            scores = predict(model, test, features)
        if not np.isfinite(scores).all():
            raise RuntimeError("Nonfinite candidate scores")
        result = test[["symbol", "trade_date"]].copy()
        result["score"], result["fold"] = scores, fold
        result.to_parquet(path, index=False)
        atomic_json(receipt, {"sha256": sha(path), "train_rows": len(train), "test_rows": len(test),
                             "train_latest_signal": train.trade_date.max(),
                             "train_latest_maturity": train["legacy_maturity" if target == "legacy" else "scheduled_exit_date"].max(),
                             "test_start": spec["test_dates"][0], "seconds": time.monotonic() - started})
        return result
    for feature_name, features in protocol["features"].items():
        for target in protocol["targets"]:
            for family, grid in GRIDS.items():
                name = f"{feature_name}_{target}_{family}"
                trials = []
                for index, params in enumerate(grid):
                    trial_name = f"{name}_config{index}"
                    cv = pd.concat([fit_fold(trial_name, family, params, features, target, fold) for fold in (1, 2, 3)], ignore_index=True)
                    metrics = quality(cv, frame, target, periods["validation"][1])
                    trials.append({"params": params, "quality": metrics, "name": trial_name, "prediction": cv})
                best = max(range(len(trials)), key=lambda i: (trials[i]["quality"]["mean_daily_rank_ic"] if trials[i]["quality"]["mean_daily_rank_ic"] is not None else -float("inf"), -i))
                chosen = trials[best]
                predictions[name] = chosen["prediction"]
                metadata[name] = {"family": family, "features": feature_name, "target": target, "params": chosen["params"],
                                  "checkpoint_name": chosen["name"], "trials": [{k: v for k, v in t.items() if k != "prediction"} for t in trials],
                                  "fold_quality": [quality(chosen["prediction"][chosen["prediction"].fold == f], frame, target, periods["validation"][1]) for f in (1, 2, 3)]}
                atomic_json(args.output / "progress.json", {"stage": "validation_fit", "candidate": name})
    sequence_tensor, sequence_mask, flat_features = None, None, None
    if args.sequence:
        print("SEQUENCE building past-only, common-calendar windows", flush=True)
        sequence_tensor, sequence_mask = sequences(frame, TECHNICAL_FEATURES, sessions)
        flat_features = [f"sequence_{i}" for i in range(sequence_tensor.shape[1] * sequence_tensor.shape[2])]
        flat = pd.DataFrame(sequence_tensor.reshape(len(frame), -1), columns=flat_features, index=frame.index)
        frame = pd.concat([frame, flat], axis=1)
        # Restrict both sequence candidates to exactly the same window-valid rows.
        for family in ("tcn", "lightgbm"):
            name = f"sequence_execution_{family}"
            params = {} if family == "tcn" else protocol["sequence_protocol"]["flattened_control"]
            prediction = pd.concat([fit_fold(name, family, params, flat_features, "execution", fold, sequence_mask, sequence_tensor) for fold in (1, 2, 3)], ignore_index=True)
            predictions[name] = prediction
            metadata[name] = {"family": family, "features": "sequence", "target": "execution", "params": params, "checkpoint_name": name,
                              "fold_quality": [quality(prediction[prediction.fold == f], frame, "execution", periods["validation"][1]) for f in (1, 2, 3)]}
    # Controls use their own score columns but the same available inference keys.
    predictions["current_reference"] = reference[reference.fold <= 3].copy()
    momentum = reference[reference.fold <= 3].copy()
    momentum["score"] = [statistics[(s, d)]["momentum"] for s, d in zip(momentum.symbol, momentum.trade_date)]
    predictions["momentum"] = momentum
    results = {name: {} for name in predictions}
    for name, prediction in predictions.items():
        print(f"EVALUATE validation {name}", flush=True)
        results[name]["validation"] = evaluate(prediction, statistics, bars, sessions, events, *periods["validation"], args.output / f"{name}_validation.json")
    winner, reasons = choose_paper_candidate({name: r["validation"] for name, r in results.items()}, metadata)
    selection = {"selected_on_validation": winner, "rejection_reasons": reasons, "metadata": metadata,
                 "assessment_used_for_selection": False, "production_changed": False}
    selection_path = args.output / "selection.json"
    if selection_path.exists() and json.loads(selection_path.read_text()) != json.loads(json.dumps(selection)):
        raise RuntimeError("Validation selection changed on resume")
    atomic_json(selection_path, selection)
    print(f"SELECTION LOCKED: {winner or 'none qualified'}; assessment fitting starts now", flush=True)
    for name, meta in metadata.items():
        seq = meta["features"] == "sequence"
        features = flat_features if seq else protocol["features"][meta["features"]]
        later = pd.concat([fit_fold(meta["checkpoint_name"], meta["family"], meta["params"], features, meta["target"], fold,
                                    sequence_mask if seq else None, sequence_tensor if seq else None) for fold in (4, 5)], ignore_index=True)
        predictions[name] = pd.concat([predictions[name], later], ignore_index=True)
        meta["assessment_quality"] = quality(later, frame, meta["target"], periods["assessment"][1])
    predictions["current_reference"] = reference
    momentum = reference.copy()
    momentum["score"] = [statistics[(s, d)]["momentum"] for s, d in zip(momentum.symbol, momentum.trade_date)]
    predictions["momentum"] = momentum
    for name, prediction in predictions.items():
        print(f"EVALUATE assessment {name}", flush=True)
        later = prediction[prediction.fold >= 4]
        results[name]["assessment"] = evaluate(later, statistics, bars, sessions, events, *periods["assessment"], args.output / f"{name}_assessment.json")
    # Freeze the validation choice for paper research even if later assessment
    # disappoints. Never replace it with the assessment leaderboard winner.
    frozen = None
    if winner:
        import joblib
        meta = metadata[winner]
        seq = meta["features"] == "sequence"
        pool = frame.loc[sequence_mask] if seq else frame
        train = training_rows(pool, meta["target"], sessions, datetime.now(IST).date().isoformat())
        model_path = args.output / "paper_model.joblib"
        if seq and meta["family"] == "tcn":
            import torch
            model = SequenceModel().fit(sequence_tensor, train.index.to_numpy(), rank_labels(train, meta["target"]), date_weights(train))
            model_path = args.output / "paper_model.pt"
            torch.save({"state_dict": model.model.state_dict(), "mean": torch.from_numpy(model.mean), "scale": torch.from_numpy(model.scale)}, model_path)
        else:
            features = flat_features if seq else protocol["features"][meta["features"]]
            model = fit_model(meta["family"], meta["params"], train, features, meta["target"])
            joblib.dump(model, model_path)
        frozen = {"candidate": winner, "status": "paper_only", "metadata": meta, "model_file": model_path.name,
                  "model_sha256": sha(model_path), "protocol_sha256": sha(protocol_path),
                  "training_latest_maturity": train["scheduled_exit_date"].max(), "frozen_at": datetime.now(IST).isoformat(),
                  "production_ready": False, "probabilities_available": False}
        atomic_json(args.output / "paper_candidate.json", frozen)
    report = {"status": "provisional", "production_changed": False, "promotion_allowed": False,
              "protocol": protocol, "periods": periods, "selected_on_validation": winner,
              "selection_rejection_reasons": reasons, "candidates": metadata, "results": results,
              "paper_tracking": {"status": "waiting_for_fresh_data" if winner else "no_candidate_qualified", "last_source_session": sessions[-1],
                                 "candidate_frozen": frozen is not None, "fresh_prospective_outcomes": 0},
              "data_quality": {"rows": len(frame), "outcomes": frame.status.value_counts().to_dict(),
                               "fundamentals": inputs["fundamentals_status"], "price_basis": "provider_adjusted"},
              "limits": ["Already researched history; later assessment is historical, not a fresh holdout.",
                         "Current-universe survivorship, adjusted execution prices and incomplete corporate-action coverage remain.",
                         "Annual statements use an estimated reporting lag; publication dates and as-reported versions are unverified. Legacy-all arms cannot qualify for paper selection.",
                         "Native rankers use different losses on the same underlying targets; LambdaRank uses ten relevance grades. This is a learner comparison, not a loss-controlled causal study.",
                         "Small fixed tuning grids, multiple candidate comparisons and dependent market regimes limit confidence.",
                         "Sequence arms have an additional past-window availability requirement; their direct architecture control is the equal-input flattened LightGBM arm.",
                         "Missing valuation outcomes stay incomplete. A historical leader is not automatically promoted.",
                         "Probability calibration and production readiness remain withheld pending sufficient independent evidence."]}
    atomic_json(args.output / "report.json", report)
    pct = lambda v: "unavailable" if v is None else f"{100*v:.2f}%"
    lines = ["# Model architecture tournament — provisional", "", f"Validation-only paper choice: {winner or 'none qualified'}. Production unchanged.", "",
             "| Candidate | Validation retail net | Later retail net | Matched momentum net | Later drawdown | Later doubled-cost net | Later delayed-entry net |",
             "|---|---:|---:|---:|---:|---:|---:|"]
    for name, r in results.items():
        v, a = r["validation"]["retail"], r["assessment"]["retail"]
        matched = r['assessment']['matched_momentum_retail']['base']['net_return']
        lines.append(f"| {name} | {pct(v['base']['net_return'])} | {pct(a['base']['net_return'])} | {pct(matched)} | {pct(a['base']['max_drawdown'])} | {pct(a['double_costs']['net_return'])} | {pct(a['entry_delayed']['net_return'])} |")
    lines += [""] + [f"- {limit}" for limit in report["limits"]]
    (args.output / "summary.md").write_text("\n".join(lines) + "\n")
    print(f"TOURNAMENT COMPLETE: {args.output / 'summary.md'}", flush=True)


if __name__ == "__main__":
    main()
