"""Bounded XGBoost/Kalman extension of the frozen 21-session tournament.

Historical research only. Never writes production models or existing paper trials.
"""

import argparse
from dataclasses import asdict
import importlib.metadata
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import numpy as np
import pandas as pd
from ml.evaluation.model_tournament import (prepare, sha, quality, evaluate,
                                           choose_paper_candidate, TECHNICAL_FEATURES)
from ml.evaluation.tournament_models import training_rows, rank_labels
from ml.evaluation.corporate_actions import load_events, crossing_events
from ml.evaluation.forward_tracking import atomic_json
from ml.evaluation.retail import Policy

GRID = [{"max_depth": 3, "n_estimators": 100}, {"max_depth": 4, "n_estimators": 150}]
KALMAN = {"measurement_variance": 1e-4, "level_process_variance": 1e-6,
          "slope_process_variance": 1e-8, "warmup": 253, "risk_window": 63, "horizon": 21}


def kalman_scores(close, resets=None):
    """Forward local-linear-trend filter; missing/event sessions reset its state.

    State = [log-price level, daily slope]. Scores scale the 21-day trend by
    trailing return risk and filtered slope uncertainty. No backward smoothing.
    """
    values = np.asarray(close, dtype=float)
    resets = np.zeros(len(values), dtype=bool) if resets is None else np.asarray(resets, dtype=bool)
    if resets.shape != values.shape:
        raise ValueError("Reset mask must match prices")
    output = np.full(len(values), np.nan)
    transition = np.array([[1., 1.], [0., 1.]])
    noise = np.diag([KALMAN["level_process_variance"], KALMAN["slope_process_variance"]])
    measurement = KALMAN["measurement_variance"]
    state, covariance, log_history = None, None, []
    for i, price in enumerate(values):
        if resets[i] or not np.isfinite(price) or price <= 0:
            state, covariance, log_history = None, None, []
            continue
        observed = np.log(price)
        if state is None:
            state = np.array([observed, 0.])
            covariance = np.diag([measurement, measurement / 100])
        else:
            state = transition @ state
            prior = transition @ covariance @ transition.T + noise
            gain = prior[:, 0] / (prior[0, 0] + measurement)
            state += gain * (observed - state[0])
            # Joseph form preserves positive semidefiniteness under rounding.
            update = np.eye(2) - np.outer(gain, [1., 0.])
            covariance = update @ prior @ update.T + measurement * np.outer(gain, gain)
        log_history.append(observed)
        if len(log_history) >= KALMAN["warmup"]:
            variance = np.var(np.diff(log_history[-KALMAN["risk_window"] - 1:]), ddof=1)
            horizon = KALMAN["horizon"]
            risk = np.sqrt(horizon * variance + horizon ** 2 * max(0., covariance[1, 1]))
            if risk > 1e-8:
                output[i] = horizon * state[1] / risk
    return output


def build_kalman(frame, bars, sessions, events):
    parts = []
    for symbol, group in frame.groupby("symbol", sort=False):
        close = [bars.get((symbol, day), {}).get("close") for day in sessions]
        resets = [bool(crossing_events(symbol, day, day, events)) for day in sessions]
        scores = pd.Series(kalman_scores(close, resets), index=sessions)
        part = group[["symbol", "trade_date"]].copy()
        part["score"] = scores.reindex(part.trade_date).to_numpy()
        parts.append(part)
    return pd.concat(parts, ignore_index=True).dropna(subset=["score"])


def fit_xgb(train, features, target, params):
    from xgboost import XGBRanker
    train = train.sort_values(["trade_date", "symbol"])
    x = train[features].to_numpy(dtype=np.float32)
    if not np.isfinite(x).all():
        raise ValueError("Nonfinite training features")
    relevance = np.minimum(9, np.floor((rank_labels(train, target) + .5) * 10)).astype(int)
    sizes = train.groupby("trade_date", sort=False).size().to_numpy()
    model = XGBRanker(objective="rank:ndcg", ndcg_exp_gain=False,
                      lambdarank_pair_method="topk", lambdarank_num_pair_per_sample=20,
                      learning_rate=.04, min_child_weight=100., reg_lambda=10.,
                      tree_method="hist", device="cpu", n_jobs=4, random_state=42,
                      subsample=1., colsample_bytree=1., **params)
    # XGBRanker weights are per-query, not per-row. Equal weights give each
    # signal date equal importance, matching the tournament's date weighting.
    model.fit(x, relevance, group=sizes, sample_weight=np.ones(len(sizes)), verbose=False)
    return model


def paired_check(result, family_size=3):
    """Three new hypotheses: two XGB targets and one fixed Kalman signal."""
    strategies = result["selection_diagnostic"]["strategies"]
    a, b = strategies["model"], strategies["momentum"]
    pairs = []
    for x, y in zip(a["cycles"], b["cycles"]):
        if x["status"] == y["status"] == "complete":
            pairs.append(x["net_return"] - y["net_return"])
    interval = None
    if len(pairs) >= 2:
        rng = np.random.default_rng(42)
        means = np.mean(rng.choice(pairs, size=(5000, len(pairs)), replace=True), axis=1)
        alpha = .05 / (2 * family_size)
        interval = list(map(float, np.quantile(means, [alpha, 1 - alpha])))
    reasons = []
    if len(pairs) < 8:
        reasons.append("Fewer than eight complete independent cycles")
    if any(x["status"] != "complete" for x in (a, b)):
        reasons.append("Incomplete equal-notional diagnostic")
    if interval is None or interval[0] <= 0:
        reasons.append("Positive cycle advantage not established after three-hypothesis adjustment")
    for scenario in ("base", "double_costs", "entry_delayed"):
        x, y = result["retail"][scenario], result["matched_momentum_retail"][scenario]
        if x["status"] != "complete" or y["status"] != "complete":
            reasons.append(f"Incomplete {scenario} comparison")
        elif x["net_return"] <= max(0., y["net_return"]):
            reasons.append(f"No positive advantage over momentum under {scenario}")
    return {"supported": not reasons, "reasons": reasons, "independent_pairs": len(pairs),
            "mean_cycle_difference": float(np.mean(pairs)) if pairs else None,
            "familywise_95pct_interval": interval, "production_ready": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "ml/evaluation/results/tournament_inputs_20261108")
    parser.add_argument("--base", type=Path, default=ROOT / "ml/evaluation/results/model_tournament_20261108")
    parser.add_argument("--output", type=Path, default=ROOT / "ml/evaluation/results/xgb_kalman_20261109")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    if (args.output / "report.json").exists():
        print("Completed experiment is frozen; choose a new output directory")
        return
    base = json.loads((args.base / "protocol.json").read_text())
    reference_dir = Path(base["reference_directory"])
    selection_path = args.base / "selection.json"
    base_selection = json.loads(selection_path.read_text())
    control_names = [f"price_only_{target}_lightgbm" for target in ("legacy", "execution")]
    control_paths = [args.base / "predictions" / f"{base_selection['metadata'][n]['checkpoint_name']}_fold{f}.parquet"
                     for n in control_names for f in range(1, 6)]
    dependencies = ["xgb_kalman.py", "model_tournament.py", "tournament_models.py", "retail.py", "compare_baselines.py",
                    "forecast_outcomes.py", "corporate_actions.py"]
    protocol = {"input_directory": str(args.input.resolve()), "input_manifest": sha(args.input / "manifest.json"),
                "base_directory": str(args.base.resolve()), "base_protocol": sha(args.base / "protocol.json"),
                "reference_files": {name: sha(reference_dir / name) for name in base["reference_files"]},
                "control_files": {str(p.resolve()): sha(p) for p in control_paths}, "base_selection": sha(selection_path),
                "source_hashes": {name: sha(ROOT / "ml/evaluation" / name) for name in dependencies},
                "versions": {n: importlib.metadata.version(n) for n in ("numpy", "pandas", "scikit-learn", "xgboost-cpu")},
                "features": TECHNICAL_FEATURES, "targets": ["legacy", "execution"], "xgb_grid": GRID,
                "xgb_fixed": {"objective": "rank:ndcg", "ndcg_exp_gain": False, "learning_rate": .04,
                              "min_child_weight": 100., "reg_lambda": 10., "topk": 20, "seed": 42},
                "kalman": KALMAN, "retail_policy": asdict(Policy()), "embargo_sessions": 63,
                "validation_folds": [1, 2, 3], "assessment_folds": [4, 5],
                "configuration_selection": "Highest mean daily rank IC in original validation folds, lower index breaks ties; no early stopping",
                "candidate_selection": "Original execution-target validation gate, all three retail scenarios; no promotion",
                "sample": "All candidates and controls share reference keys with causal 253-consecutive-close Kalman availability; no outcome filter",
                "assessment": "Original later historical folds; independent cycles, bootstrap interval adjusted for three new hypotheses"}
    protocol_path = args.output / "protocol.json"
    if protocol_path.exists() and json.loads(protocol_path.read_text()) != protocol:
        raise RuntimeError("Protocol changed; use a new output")
    atomic_json(protocol_path, protocol)
    print("XGB/KALMAN protocol locked", flush=True)
    # Read validated existing outcome checkpoint; never rewrite the base run.
    frame, bars, sessions, statistics, _ = prepare(args.input, args.base)
    events = load_events()
    references = []
    folds = {}
    for fold in range(1, 6):
        name = f"rank21_fold{fold}.csv"
        if sha(reference_dir / name) != base["reference_files"][name]:
            raise RuntimeError("Reference predictions changed")
        p = pd.read_csv(reference_dir / name)
        p["fold"] = fold
        references.append(p)
        days = sorted(p.trade_date.unique())
        folds[fold] = {"test_dates": days, "train_dates": sessions[:sessions.index(days[0]) - 63]}
    reference = pd.concat(references, ignore_index=True)
    kalman = build_kalman(frame, bars, sessions, events).merge(reference[["symbol", "trade_date", "fold"]], validate="one_to_one")
    keys = kalman[["symbol", "trade_date", "fold"]]
    common = reference.merge(keys, on=["symbol", "trade_date", "fold"], validate="one_to_one")
    if common.empty or common.fold.nunique() != 5:
        raise RuntimeError("Common causal pool must cover all folds")
    periods = {"validation": (folds[1]["test_dates"][0], folds[3]["test_dates"][-1]),
               "assessment": (folds[4]["test_dates"][0], sessions[-1])}
    checkpoints = args.output / "predictions"
    checkpoints.mkdir(exist_ok=True)

    def fit_fold(target, config, fold):
        path = checkpoints / f"xgb_{target}_config{config}_fold{fold}.parquet"
        receipt = path.with_suffix(".json")
        if path.exists() and receipt.exists():
            if json.loads(receipt.read_text())["sha256"] != sha(path):
                raise RuntimeError("Prediction checkpoint changed")
            return pd.read_parquet(path)
        spec = folds[fold]
        train = training_rows(frame, target, spec["train_dates"], spec["test_dates"][0])
        test = frame.merge(keys[keys.fold == fold], on=["symbol", "trade_date"], validate="one_to_one")
        print(f"FIT XGB {target} config{config} fold{fold}: {len(train)} / {len(test)}", flush=True)
        start = time.monotonic()
        model = fit_xgb(train, TECHNICAL_FEATURES, target, GRID[config])
        scores = model.predict(test[TECHNICAL_FEATURES].to_numpy(dtype=np.float32))
        if not np.isfinite(scores).all():
            raise RuntimeError("Nonfinite predictions")
        p = test[["symbol", "trade_date", "fold"]].copy()
        p["score"] = scores
        p.to_parquet(path, index=False)
        maturity = "legacy_maturity" if target == "legacy" else "scheduled_exit_date"
        atomic_json(receipt, {"sha256": sha(path), "train_rows": len(train), "test_rows": len(test),
                             "train_latest_signal": train.trade_date.max(), "train_latest_maturity": train[maturity].max(),
                             "test_start": spec["test_dates"][0], "seconds": time.monotonic() - start})
        return p

    predictions = {"kalman_trend": kalman, "current_model_reference": common}
    for name in control_names:
        files = [args.base / "predictions" / f"{base_selection['metadata'][name]['checkpoint_name']}_fold{f}.parquet" for f in range(1, 6)]
        p = pd.concat([pd.read_parquet(path) for path in files], ignore_index=True)
        p = p.merge(keys, on=["symbol", "trade_date", "fold"], validate="one_to_one")
        if len(p) != len(keys):
            raise RuntimeError("Control inference keys differ")
        predictions[name] = p
    metadata = {"kalman_trend": {"features": "price_only", "target": "execution",
                                "fold_quality": [quality(kalman[kalman.fold == f], frame, "execution", periods["validation"][1]) for f in (1, 2, 3)]}}
    for target in ("legacy", "execution"):
        trials = []
        for config in range(len(GRID)):
            p = pd.concat([fit_fold(target, config, f) for f in (1, 2, 3)], ignore_index=True)
            trials.append((quality(p, frame, target, periods["validation"][1]), p))
        best = max(range(len(GRID)), key=lambda i: (trials[i][0]["mean_daily_rank_ic"] if trials[i][0]["mean_daily_rank_ic"] is not None else -np.inf, -i))
        name = f"xgb_{target}"
        predictions[name] = trials[best][1]
        metadata[name] = {"features": "price_only", "target": target, "config": best, "params": GRID[best],
                          "trials": [{"params": GRID[i], "quality": q} for i, (q, _) in enumerate(trials)],
                          "fold_quality": [quality(predictions[name][predictions[name].fold == f], frame, target, periods["validation"][1]) for f in (1, 2, 3)]}
    results = {}
    for name, p in predictions.items():
        print(f"VALIDATION {name}", flush=True)
        results[name] = {"validation": evaluate(p[p.fold <= 3], statistics, bars, sessions, events, *periods["validation"], args.output / f"{name}_validation.json")}
    winner, reasons = choose_paper_candidate({n: r["validation"] for n, r in results.items()}, metadata)
    selection = {"selected_on_validation": winner, "rejection_reasons": reasons, "metadata": metadata, "assessment_used": False}
    selection_file = args.output / "selection.json"
    if selection_file.exists() and json.loads(selection_file.read_text()) != selection:
        raise RuntimeError("Validation selection changed on resume")
    atomic_json(selection_file, selection)
    print(f"SELECTION locked: {winner}", flush=True)
    for target in ("legacy", "execution"):
        name = f"xgb_{target}"
        predictions[name] = pd.concat([predictions[name]] + [fit_fold(target, metadata[name]["config"], f) for f in (4, 5)], ignore_index=True)
    checks = {}
    for name, p in predictions.items():
        later = p[p.fold >= 4]
        later.to_parquet(args.output / f"{name}_assessment_signals.parquet", index=False)
        print(f"ASSESSMENT {name}", flush=True)
        results[name]["assessment"] = evaluate(later, statistics, bars, sessions, events, *periods["assessment"], args.output / f"{name}_assessment.json")
        if name in metadata:
            checks[name] = paired_check(results[name]["assessment"])
            metadata[name]["assessment_quality"] = quality(later, frame, metadata[name]["target"], periods["assessment"][1])
    report = {"status": "historical_research", "production_changed": False, "promotion_allowed": False,
              "protocol": protocol, "periods": periods, "selected_on_validation": winner,
              "rejection_reasons": reasons, "metadata": metadata, "results": results, "assessment_checks": checks,
              "coverage": {"rows": len(keys), "dates": keys.trade_date.nunique(), "symbols": keys.symbol.nunique(),
                           "original_reference_rows": len(reference), "retained_fraction": len(keys) / len(reference)},
              "limits": ["Previously examined historical data; not a fresh holdout or prospective trading evidence.",
                         "Current-universe/industry taxonomy, provider-adjusted prices and unresolved entitlement valuations remain.",
                         "Kalman uses longer price history; tree inputs remain the existing technical features. This is a strategy comparison, not an equal-information architecture claim.",
                         "Fixed Kalman variance values are assumptions; only two XGBoost configurations per target were tested.",
                         "No production model, existing frozen paper candidate or trading rule was changed."]}
    atomic_json(args.output / "report.json", report)
    pct = lambda v: "unknown" if v is None else f"{100*v:.2f}%"
    lines = ["# XGBoost and Kalman — historical research", "", f"Validation choice: {winner or 'none qualified'}. No production promotion.", "",
             "| Candidate | Validation net | Later net | Matched momentum | Later drawdown | Double costs | Entry delayed |", "|---|---:|---:|---:|---:|---:|---:|"]
    for name, r in results.items():
        v, a = r["validation"]["retail"], r["assessment"]["retail"]
        lines.append(f"| {name} | {pct(v['base']['net_return'])} | {pct(a['base']['net_return'])} | {pct(r['assessment']['matched_momentum_retail']['base']['net_return'])} | {pct(a['base']['max_drawdown'])} | {pct(a['double_costs']['net_return'])} | {pct(a['entry_delayed']['net_return'])} |")
    lines += [""] + [f"- {s}" for s in report["limits"]]
    (args.output / "summary.md").write_text("\n".join(lines) + "\n")
    print(f"COMPLETE {args.output / 'summary.md'}", flush=True)


if __name__ == "__main__":
    main()
