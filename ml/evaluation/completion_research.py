"""Complete remaining context, target, verified-data and robustness experiments.

Separate frozen research version; existing tournament/production are unchanged.
"""

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "ml/feature_engineering")]
import numpy as np
import pandas as pd
from transform import TECHNICAL_FEATURES
from ml.evaluation.model_tournament import prepare, sha, evaluate, choose_paper_candidate
from ml.evaluation.forward_tracking import atomic_json, IST
from ml.evaluation.corporate_actions import load_events
from ml.evaluation.retail import Policy, eligibility, simulate
from ml.evaluation.tournament_models import SequenceModel, sequences, rank_labels, date_weights
from ml.evaluation.walk_forward import expanding_folds
from ml.evaluation.research_context import add_context, CONTEXT_FEATURES, portfolio_robustness
from ml.evaluation.verified_financials import audit, attach, FEATURES as FINANCIAL_FEATURES
from ml.evaluation.completion_models import Learner, fit_sigmoid
from ml.evaluation.calibration import assess_probability


def add_targets(frame, statistics, events):
    result = frame.copy()
    result["eligible"] = [not eligibility({**statistics.get((s, d), {}), "rank": 1}, Policy(), d, events)
                          and statistics.get((s, d), {}).get("momentum") is not None for s, d in zip(result.symbol, result.trade_date)]
    eligible = result[result.eligible]
    group = eligible.groupby("trade_date").net_return
    means = group.mean().where(group.count() == group.size())
    result["benchmark_net"] = result.trade_date.map(means)
    result["excess_net"] = result.net_return - result.benchmark_net
    result["profit_label"] = (result.net_return > 0).astype(float).where(result.net_return.notna())
    result["outperform_label"] = (result.excess_net > 0).astype(float).where(result.excess_net.notna())
    return result


def probability_report(prediction, frame, objective, assessment_start, through):
    label = "profit_label" if objective == "profit" else "outperform_label"
    merged = prediction.merge(frame[["symbol", "trade_date", "eligible", label, "scheduled_exit_date"]], on=["symbol", "trade_date"], validate="one_to_one")
    merged = merged[merged.eligible].copy()
    merged["rank"] = merged.groupby("trade_date").score.rank(method="first", ascending=False)
    merged["logit_probability"] = np.log(merged.score.clip(1e-6, 1 - 1e-6) / (1 - merged.score.clip(1e-6, 1 - 1e-6)))
    merged = merged.rename(columns={"trade_date": "signal_date", label: "target"})
    calibration = merged[(merged.fold == 3) & merged.scheduled_exit_date.notna() & (merged.scheduled_exit_date < assessment_start) & merged.target.notna()]
    later = merged[(merged.fold >= 4) & merged.scheduled_exit_date.notna() & (merged.scheduled_exit_date <= through)]
    fit = fit_sigmoid(calibration.to_dict("records"), "target")
    if fit is None:
        return {"status": "insufficient_calibration", "production_ready": False, "calibration_rows": len(calibration)}
    raw = {"features": ["logit_probability"], "mean": [0.], "scale": [1.], "coefficients": [0., 1.], "baseline_probability": fit["baseline_probability"]}
    scopes = {}
    for scope, sample in [("all_eligible", later), ("top20", later[later["rank"] <= 20])]:
        missing = 1 - sample.target.notna().mean() if len(sample) else 1.
        rows = sample[sample.target.notna()].to_dict("records")
        metrics = {"raw": assess_probability(raw, rows, "target", 21), "calibrated": assess_probability(fit, rows, "target", 21)}
        for metric in metrics.values():
            if missing > .02:
                metric["passed"] = False
                metric["reasons"].append("More than 2% of selected matured targets are unavailable")
        scopes[scope] = {**metrics, "missing_matured_fraction": float(missing), "attempted_rows": len(sample)}
    return {"status": "historical_only", "production_ready": False, "calibration_model": fit,
            "calibration_last_maturity": calibration.scheduled_exit_date.max(), "assessment_start": assessment_start,
            "scopes": scopes, "limits": ["Calibrator fits fold-3 out-of-sample probabilities whose scheduled outcomes mature before fold 4.",
                                         "No calibrated estimate is served live from this research artifact."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "ml/evaluation/results/tournament_inputs_20261108")
    parser.add_argument("--base", type=Path, default=ROOT / "ml/evaluation/results/model_tournament_20261108")
    parser.add_argument("--filings", type=Path, default=ROOT / "ml/evaluation/results/remaining_plan_20261108/filings")
    parser.add_argument("--output", type=Path, default=ROOT / "ml/evaluation/results/remaining_plan_20261108/run")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    if (args.output / "report.json").exists():
        print("Completed extension is frozen; choose a new output for another experiment")
        return
    sources = ["completion_research.py", "completion_models.py", "research_context.py", "verified_financials.py"]
    financials = audit(args.filings, datetime.now(IST).date().isoformat())
    atomic_json(args.output / "financial_audit.json", financials)
    specs = {
        "technical_rank_control": ("lightgbm", "rank", False, False),
        "context_rank_linear": ("linear", "rank", True, True),
        "context_rank_lightgbm": ("lightgbm", "rank", True, True),
        "context_rank_catboost_native": ("catboost", "rank", True, True),
        "profit_lightgbm": ("lightgbm", "profit", True, True),
        "profit_catboost_native": ("catboost", "profit", True, True),
        "outperform_lightgbm": ("lightgbm", "outperform", True, True),
        "outperform_catboost_native": ("catboost", "outperform", True, True),
        "excess_net_lightgbm": ("lightgbm", "excess", True, True),
    }
    protocol = {"input_manifest": sha(args.input / "manifest.json"), "base_protocol": sha(args.base / "protocol.json"),
                "source_hashes": {n: sha(ROOT / "ml/evaluation" / n) for n in sources},
                "filing_download_index": sha(args.filings / "downloads.json"), "financial_audit": sha(args.output / "financial_audit.json"),
                "specs": specs, "features": TECHNICAL_FEATURES + CONTEXT_FEATURES,
                "fixed_budget": "One prespecified configuration per arm, five chronological folds; no assessment tuning",
                "context_sequence": "20 past sessions of technical/market/sector features and explicit context-missing flags; fixed TCN and identical flattened-window tree control",
                "benchmark": "Same-date eligible equal-notional net stop-policy outcomes; unavailable unless every constituent outcome is known",
                "probability_protocol": "Fold 3 Platt calibration, scheduled maturity before fold 4; assess folds 4-5 with date/block weighting",
                "robustness": "Quarter/regime/exposure/beta/stock and industry contribution; remove top validation contributor in later simulation only",
                "verified_financial_protocol": "Three expanding folds over the verified covered subset, 22-session embargo, same rows for context-only control and financial augmentation",
                "production_changed": False, "promotion_allowed": False}
    path = args.output / "protocol.json"
    if path.exists() and json.loads(path.read_text()) != json.loads(json.dumps(protocol)):
        raise RuntimeError("Extension protocol changed; choose a new output")
    atomic_json(path, protocol)
    print("COMPLETION protocol frozen", flush=True)
    frame, bars, sessions, statistics, _ = prepare(args.input, args.base)
    events = load_events()
    frame = add_targets(add_context(frame, bars, sessions, statistics), statistics, events)
    base_protocol = json.loads((args.base / "protocol.json").read_text())
    reference = Path(base_protocol["reference_directory"])
    folds, keys = {}, {}
    for fold in range(1, 6):
        keys[fold] = pd.read_csv(reference / f"rank21_fold{fold}.csv", usecols=["symbol", "trade_date"])
        days = sorted(keys[fold].trade_date.unique())
        folds[fold] = {"train_dates": sessions[:sessions.index(days[0]) - 63], "test_dates": days}
    periods = {"validation": (folds[1]["test_dates"][0], folds[3]["test_dates"][-1]), "assessment": (folds[4]["test_dates"][0], sessions[-1])}
    thresholds = frame[frame.trade_date < folds[1]["test_dates"][0]].drop_duplicates("trade_date").market_volatility_20d
    volatility_cutoff = float(thresholds.median())
    predictions, models, metadata = {}, {}, {}
    cache = args.output / "predictions"
    cache.mkdir(exist_ok=True)
    for name, (family, objective, context, categorical) in specs.items():
        features = TECHNICAL_FEATURES + (CONTEXT_FEATURES if context else [])
        predictions[name] = []
        metadata[name] = {"family": family, "objective": objective, "features": features, "categorical_industry": categorical}
    def fit_fold(name, fold):
        family, objective, context, categorical = specs[name]
        features = metadata[name]["features"]
        file = cache / f"{name}_fold{fold}.parquet"
        if file.exists():
            receipt = json.loads(file.with_suffix(".json").read_text())
            if sha(file) != receipt["sha256"]:
                raise RuntimeError("Prediction checkpoint changed")
            return pd.read_parquet(file)
        label = {"rank": "net_return", "profit": "profit_label", "outperform": "outperform_label", "excess": "excess_net"}[objective]
        spec = folds[fold]
        train = frame[frame.trade_date.isin(spec["train_dates"]) & frame.eligible & frame[label].notna() & frame.scheduled_exit_date.notna() & (frame.scheduled_exit_date < spec["test_dates"][0])]
        test = frame.merge(keys[fold], on=["symbol", "trade_date"], validate="one_to_one")
        print(f"FIT {name} fold {fold}: {len(train)} train", flush=True)
        model = Learner(family, objective, features, categorical).fit(train)
        prediction = test[["symbol", "trade_date"]].copy()
        prediction["score"], prediction["fold"] = model.predict(test), fold
        prediction.to_parquet(file, index=False)
        atomic_json(file.with_suffix(".json"), {"sha256": sha(file), "train_latest_maturity": train.scheduled_exit_date.max(), "test_start": spec["test_dates"][0], "rows": len(test)})
        return prediction
    results = {name: {} for name in specs}
    for name in specs:
        predictions[name] = pd.concat([fit_fold(name, f) for f in (1, 2, 3)], ignore_index=True)
        print(f"EVALUATE validation {name}", flush=True)
        results[name]["validation"] = evaluate(predictions[name], statistics, bars, sessions, events, *periods["validation"], args.output / f"{name}_validation.json")
    # Constant zero plus explicit missing flags is a prespecified representation,
    # not a median fitted using later data. Neural scaling still fits train only.
    sequence_columns = TECHNICAL_FEATURES + CONTEXT_FEATURES
    sequence_frame = frame[sequence_columns + ["symbol", "trade_date"]].copy()
    for column in CONTEXT_FEATURES:
        sequence_frame[column + "_missing"] = sequence_frame[column].isna().astype(float)
        sequence_frame[column] = sequence_frame[column].fillna(0.)
    sequence_columns += [c + "_missing" for c in CONTEXT_FEATURES]
    tensor, sequence_valid = sequences(sequence_frame, sequence_columns, sessions)
    flat_features = [f"context_sequence_{i}" for i in range(tensor.shape[1] * tensor.shape[2])]
    flat = pd.DataFrame(tensor.reshape(len(frame), -1), index=frame.index, columns=flat_features)
    sequence_pool = pd.concat([frame, flat], axis=1)
    def sequence_fold(name, family, fold):
        file = cache / f"{name}_fold{fold}.parquet"
        if file.exists():
            if sha(file) != json.loads(file.with_suffix(".json").read_text())["sha256"]:
                raise RuntimeError("Sequence checkpoint changed")
            return pd.read_parquet(file)
        spec = folds[fold]
        train = sequence_pool[sequence_valid & sequence_pool.eligible & sequence_pool.net_return.notna() &
                              sequence_pool.trade_date.isin(spec["train_dates"]) & (sequence_pool.scheduled_exit_date < spec["test_dates"][0])]
        test = sequence_pool[sequence_valid].reset_index(names="row_index").merge(keys[fold], on=["symbol", "trade_date"], validate="one_to_one").set_index("row_index")
        print(f"FIT {name} fold {fold}: {len(train)}", flush=True)
        if family == "tcn":
            model = SequenceModel().fit(tensor, train.index.to_numpy(), rank_labels(train, "execution"), date_weights(train))
            scores = model.predict(tensor, test.index.to_numpy())
        else:
            model = Learner("lightgbm", "rank", flat_features, False).fit(train)
            scores = model.predict(test)
        p = test[["symbol", "trade_date"]].copy()
        p["score"], p["fold"] = scores, fold
        p.to_parquet(file, index=False)
        atomic_json(file.with_suffix(".json"), {"sha256": sha(file), "train_latest_maturity": train.scheduled_exit_date.max(), "test_start": spec["test_dates"][0]})
        return p
    for family in ("tcn", "lightgbm"):
        name = f"context_sequence_{family}"
        predictions[name] = pd.concat([sequence_fold(name, family, f) for f in (1, 2, 3)], ignore_index=True)
        results[name] = {"validation": evaluate(predictions[name], statistics, bars, sessions, events, *periods["validation"], args.output / f"{name}_validation.json")}
    all_names = list(results)
    # This choice is fixed before any later predictions are fitted or assessed.
    eligible_names = [n for n in all_names if results[n]["validation"]["retail"]["base"]["status"] == "complete" and
                      results[n]["validation"]["retail"]["base"]["net_return"] > 0 and all(
                      results[n]["validation"]["retail"][s]["status"] == results[n]["validation"]["matched_momentum_retail"][s]["status"] == "complete" and
                      results[n]["validation"]["retail"][s]["net_return"] > results[n]["validation"]["matched_momentum_retail"][s]["net_return"]
                      for s in ("base", "double_costs", "entry_delayed")) and
                      results[n]["validation"]["retail"]["base"]["max_drawdown"] >= -.25]
    winner = max(eligible_names, key=lambda n: results[n]["validation"]["retail"]["base"]["net_return"]) if eligible_names else None
    atomic_json(args.output / "selection.json", {"selected_on_validation": winner, "assessment_used": False})
    print(f"COMPLETION selection locked: {winner}", flush=True)
    calibration, robustness = {}, {}
    for name in all_names:
        if name.startswith("context_sequence_"):
            family = "tcn" if name.endswith("_tcn") else "lightgbm"
            later = pd.concat([sequence_fold(name, family, f) for f in (4, 5)], ignore_index=True)
        else:
            later = pd.concat([fit_fold(name, f) for f in (4, 5)], ignore_index=True)
        predictions[name] = pd.concat([predictions[name], later], ignore_index=True)
        results[name]["assessment"] = evaluate(later, statistics, bars, sessions, events, *periods["assessment"], args.output / f"{name}_assessment.json")
        detailed = json.loads((args.output / f"{name}_assessment.json").read_text())
        robustness[name] = {strategy: portfolio_robustness(detailed["retail"][strategy]["base"], frame, volatility_cutoff)
                            for strategy in ("candidate", "matched_momentum")}
        if name in specs and specs[name][1] in ("profit", "outperform"):
            calibration[name] = probability_report(predictions[name], frame, specs[name][1], periods["assessment"][0], sessions[-1])
    # A no-top-stock sensitivity uses the winner's top *validation* contributor,
    # not a stock chosen after seeing later profits.
    ablation = None
    if winner:
        detailed = json.loads((args.output / f"{winner}_validation.json").read_text())
        trades = pd.DataFrame(detailed["retail"]["candidate"]["base"]["trades"])
        if len(trades):
            removed = trades.groupby("symbol").pnl.sum().idxmax()
            later = predictions[winner]
            later = later[(later.fold >= 4) & (later.symbol != removed)]
            ablation = {"removed_symbol": removed, "chosen_using": "validation closed-trade contribution only",
                        "result": evaluate(later, statistics, bars, sessions, events, *periods["assessment"], args.output / "winner_without_validation_top_stock.json")}
    # Verified financial experiment: identical eligible observed rows, a context
    # control and an augmented model; no neutral-filled legacy Yahoo statements.
    verified = attach(frame, financials["accepted"])
    covered = verified[verified.verified_source.notna() & verified.eligible].copy()
    counts = covered.groupby("trade_date").symbol.nunique()
    days = sorted(counts[counts >= 20].index)
    financial_result = {"status": "insufficient_history", "verified_filings": len(financials["accepted"]), "symbols": financials["symbols"], "covered_dates": len(days)}
    if len(days) >= 180:
        financial_folds = expanding_folds(days, n_folds=3, min_train_fraction=.5, test_fraction=.15, embargo_days=22)
        arms = {}
        for augmented in (False, True):
            arm = "verified_financials" if augmented else "matched_context_control"
            features = TECHNICAL_FEATURES + CONTEXT_FEATURES + (FINANCIAL_FEATURES if augmented else [])
            parts = []
            for fold, spec in enumerate(financial_folds, 1):
                train = covered[covered.trade_date.isin(spec["train_dates"]) & covered.net_return.notna() & (covered.scheduled_exit_date < spec["test_dates"][0])]
                test = covered[covered.trade_date.isin(spec["test_dates"])]
                print(f"FIT {arm} fold {fold}: {len(train)}", flush=True)
                model = Learner("lightgbm", "rank", features, True).fit(train)
                p = test[["symbol", "trade_date"]].copy()
                p["score"], p["fold"] = model.predict(test), fold
                parts.append(p)
            prediction = pd.concat(parts, ignore_index=True)
            prediction.to_parquet(cache / f"{arm}.parquet", index=False)
            start, end = prediction.trade_date.min(), prediction.trade_date.max()
            arms[arm] = evaluate(prediction, statistics, bars, sessions, events, start, end, args.output / f"{arm}.json")
        financial_result.update(status="historical_subset_tested", first_date=days[0], last_date=days[-1], arms=arms,
                                limits=["Restricted verified corpus and one-year age cap; this is a separate shorter matched experiment, not the original five-fold test.",
                                        "Corpus completeness is not assumed and no financial model is promoted."])
    if winner:
        import joblib
        sequence = winner.startswith("context_sequence_")
        label = "net_return" if sequence else {"rank": "net_return", "profit": "profit_label", "outperform": "outperform_label", "excess": "excess_net"}[specs[winner][1]]
        pool = sequence_pool if sequence else frame
        train = pool[pool.eligible & pool[label].notna() & pool.scheduled_exit_date.notna() &
                     (pool.scheduled_exit_date < datetime.now(IST).date().isoformat())]
        if sequence:
            train = train[sequence_valid[train.index]]
        if sequence and winner.endswith("_tcn"):
            import torch
            model = SequenceModel().fit(tensor, train.index.to_numpy(), rank_labels(train, "execution"), date_weights(train))
            model_file = args.output / "paper_model.pt"
            torch.save({"state_dict": model.model.state_dict(), "mean": torch.from_numpy(model.mean), "scale": torch.from_numpy(model.scale)}, model_file)
        else:
            if sequence:
                model = Learner("lightgbm", "rank", flat_features, False).fit(train)
            else:
                model = Learner(specs[winner][0], specs[winner][1], metadata[winner]["features"], specs[winner][3]).fit(train)
            model_file = args.output / "paper_model.joblib"
            joblib.dump(model, model_file)
        atomic_json(args.output / "paper_candidate.json", {"candidate": winner, "status": "paper_only", "model_file": model_file.name,
                    "model_sha256": sha(model_file), "protocol_sha256": sha(path), "sequence": sequence,
                    "sequence_columns": sequence_columns if sequence else None, "frozen_at": datetime.now(IST).isoformat(),
                    "training_latest_maturity": train.scheduled_exit_date.max(), "production_ready": False})
    report = {"status": "historical_research", "production_changed": False, "promotion_allowed": False,
              "protocol": protocol, "periods": periods, "selected_on_validation": winner, "results": results,
              "probability_calibration": calibration, "robustness": robustness, "validation_top_stock_ablation": ablation,
              "verified_financial_experiment": financial_result,
              "sequence_context_status": "Contextual TCN and equal-input flattened tree control tested on identical past windows",
              "limits": ["Previously researched history; no fresh holdout or guaranteed improvement is claimed.",
                         "Adjusted execution prices, unresolved entitlements and current-universe/industry metadata remain limitations.",
                         "Outperformance benchmarks require every eligible outcome; benchmark gaps are reported, not imputed.",
                         "Live confidence and automatic model promotion remain disabled."]}
    atomic_json(args.output / "report.json", report)
    print(f"COMPLETION COMPLETE: {args.output / 'report.json'}", flush=True)


if __name__ == "__main__":
    main()
