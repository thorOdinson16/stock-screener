"""Fixed mathematical signal tournament; no fitted ML model or formula search."""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT)]
import numpy as np
import pandas as pd
from ml.evaluation.model_tournament import prepare, sha, evaluate, quality, choose_paper_candidate
from ml.evaluation.forward_tracking import atomic_json
from ml.evaluation.corporate_actions import load_events, crossing_events
from ml.evaluation.research_context import add_context, portfolio_robustness

FORMULAS = {
    "momentum_12_minus_1": "log(P[t-21] / P[t-252])",
    "risk_adjusted_momentum": "log(P[t-21] / P[t-252]) / (std63(log returns) * sqrt(231))",
    "market_residual_momentum": "sum63(r - alpha252 - beta252*m) / (residual_std252 * sqrt(63)); coefficients estimated at signal date",
    "mean_reversion_63": "-(log(P[t]) - mean63(log(P))) / std63(log(P))",
    "breakout_63": "log(P[t] / max(P[t-63:t-1])) / std63(log returns)",
    "fixed_rank_blend": "(percentile(momentum_12_minus_1) + percentile(-vol63) + percentile(sector-relative momentum63)) / 3",
}


def symbol_equations(close, market_close):
    """Full common-calendar windows, no filling gaps or future observations."""
    close = close.where(close > 0)
    market_close = market_close.where(market_close > 0)
    logp, logm = np.log(close), np.log(market_close)
    r, m = logp.diff(), logm.diff()
    vol = r.rolling(63, min_periods=63).std().where(lambda x: x > 1e-8)
    skipped = logp.shift(21) - logp.shift(252)
    # Endpoints alone cannot prove that a suspended stock supplied a full
    # history. Require all 253 closes for the 252-return estimation window.
    complete = close.rolling(253, min_periods=253).count() == 253
    beta = r.rolling(252, min_periods=252).cov(m) / m.rolling(252, min_periods=252).var().where(lambda x: x > 1e-12)
    alpha = r.rolling(252, min_periods=252).mean() - beta * m.rolling(252, min_periods=252).mean()
    residual_variance = (r.rolling(252, min_periods=252).var() - beta * r.rolling(252, min_periods=252).cov(m)).clip(lower=0)
    residual_std = np.sqrt(residual_variance).where(lambda x: x > 1e-8)
    residual_sum = r.rolling(63, min_periods=63).sum() - 63 * alpha - beta * m.rolling(63, min_periods=63).sum()
    price_std = logp.rolling(63, min_periods=63).std().where(lambda x: x > 1e-8)
    result = pd.DataFrame({
        "momentum_12_minus_1": skipped,
        "risk_adjusted_momentum": skipped / (vol * np.sqrt(231)),
        "market_residual_momentum": residual_sum / (residual_std * np.sqrt(63)),
        "mean_reversion_63": -(logp - logp.rolling(63, min_periods=63).mean()) / price_std,
        "breakout_63": (logp - np.log(close.shift(1).rolling(63, min_periods=63).max())) / vol,
        "vol63": vol,
        "momentum63": close / close.shift(63) - 1,
    })
    result.loc[~complete, :] = np.nan
    return result.replace([np.inf, -np.inf], np.nan)


def build_scores(frame, bars, sessions, events):
    market = pd.Series([bars[("^NSEI", day)]["close"] for day in sessions], index=sessions, dtype=float)
    parts = []
    for symbol, group in frame.groupby("symbol", sort=False):
        close = pd.Series([bars.get((symbol, day), {}).get("close") for day in sessions], index=sessions, dtype=float)
        scores = symbol_equations(close, market)
        for event in events:
            if symbol in event["symbols"]:
                # Price-history resets must also cover the full formula window.
                blocked = [day for i, day in enumerate(sessions) if crossing_events(symbol, sessions[max(0, i - 252)], day, [event])]
                scores.loc[blocked, :] = np.nan
        scores = scores.reindex(group.trade_date)
        scores["symbol"], scores["trade_date"] = symbol, group.trade_date.to_numpy()
        parts.append(scores.reset_index(drop=True))
    result = frame[["symbol", "trade_date", "industry"]].merge(pd.concat(parts, ignore_index=True), on=["symbol", "trade_date"], validate="one_to_one")
    # One common, past-data availability pool for all formulas and controls.
    result = result.dropna(subset=list(FORMULAS)[:-1] + ["vol63", "momentum63"]).copy()
    groups = result.groupby(["trade_date", "industry"], dropna=False).momentum63
    count = groups.transform("count")
    peer = ((groups.transform("sum") - result.momentum63) / (count - 1)).where(count >= 2)
    # A singleton industry supplies no relative signal: fixed neutral value,
    # not a replacement sector selected using subsequent performance.
    relative = (result.momentum63 - peer).fillna(0.)
    result["fixed_rank_blend"] = (result.groupby("trade_date").momentum_12_minus_1.rank(pct=True) +
                                  (-result.vol63).groupby(result.trade_date).rank(pct=True) +
                                  relative.groupby(result.trade_date).rank(pct=True)) / 3
    return result


def adjusted_pair_check(result):
    diagnostic = result["selection_diagnostic"]["strategies"]
    pairs = [(a["net_return"], b["net_return"]) for a, b in zip(diagnostic["model"]["cycles"], diagnostic["momentum"]["cycles"])
             if a["status"] == b["status"] == "complete"]
    differences = np.asarray([a - b for a, b in pairs])
    interval = None
    if len(differences) >= 2:
        rng = np.random.default_rng(42)
        draws = np.asarray([rng.choice(differences, len(differences), replace=True).mean() for _ in range(2000)])
        interval = list(map(float, np.quantile(draws, [.05 / (2 * len(FORMULAS)), 1 - .05 / (2 * len(FORMULAS))])))
    reasons = []
    if len(pairs) < 8:
        reasons.append("Fewer than eight complete independent cycles")
    if any(diagnostic[n]["status"] != "complete" for n in ("model", "momentum")):
        reasons.append("Full equal-notional comparison is incomplete")
    if interval is None or interval[0] <= 0:
        reasons.append("Positive advantage is not established after adjusting for six formulas")
    for scenario in ("base", "double_costs", "entry_delayed"):
        a, b = result["retail"][scenario], result["matched_momentum_retail"][scenario]
        if a["status"] != b["status"] or a["status"] != "complete":
            reasons.append(f"Incomplete {scenario} portfolio comparison")
        elif a["net_return"] <= max(0., b["net_return"]):
            reasons.append(f"No positive advantage over momentum under {scenario}")
    return {"supported": not reasons, "reasons": reasons, "independent_pairs": len(pairs),
            "mean_cycle_difference": float(differences.mean()) if len(pairs) else None,
            "familywise_95pct_interval": interval, "production_ready": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "ml/evaluation/results/tournament_inputs_20261108")
    parser.add_argument("--base", type=Path, default=ROOT / "ml/evaluation/results/model_tournament_20261108")
    parser.add_argument("--output", type=Path, default=ROOT / "ml/evaluation/results/quant_equations_20261109")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    if (args.output / "report.json").exists():
        print("Equation experiment is frozen; choose a new output for changes")
        return
    base_protocol = json.loads((args.base / "protocol.json").read_text())
    dependencies = {name: sha(ROOT / "ml/evaluation" / name) for name in
                    ("quant_equations.py", "model_tournament.py", "retail.py", "compare_baselines.py", "corporate_actions.py", "research_context.py")}
    protocol = {"input_directory": str(args.input.resolve()), "input_manifest": sha(args.input / "manifest.json"),
                "base_directory": str(args.base.resolve()), "base_protocol": sha(args.base / "protocol.json"),
                "source_hashes": dependencies, "formulas": FORMULAS,
                "parameters": {"momentum": [252, 21], "risk_window": 63, "regression_window": 252, "reversal_window": 63, "breakout_window": 63},
                "selection": "Original validation-only retail/IC gate; all three cost/entry scenarios; no formula/weight/window optimization",
                "assessment": "Original later folds; paired independent-cycle interval adjusted across six formulas; no production promotion",
                "sample": "Common inference keys with full 253-close common-calendar history and all six finite scores; no future-outcome filtering"}
    protocol_file = args.output / "protocol.json"
    if protocol_file.exists() and json.loads(protocol_file.read_text()) != protocol:
        raise RuntimeError("Equation protocol changed; choose a new output directory")
    atomic_json(protocol_file, protocol)
    print("QUANT equation protocol frozen", flush=True)
    frame, bars, sessions, statistics, _ = prepare(args.input, args.base)
    events = load_events()
    scores = build_scores(frame, bars, sessions, events)
    reference = Path(base_protocol["reference_directory"])
    parts, folds = [], {}
    for fold in range(1, 6):
        p = pd.read_csv(reference / f"rank21_fold{fold}.csv")
        p["fold"] = fold
        parts.append(p)
        folds[fold] = sorted(p.trade_date.unique())
    original = pd.concat(parts, ignore_index=True)
    scored = scores.merge(original[["symbol", "trade_date", "fold"]], on=["symbol", "trade_date"], validate="one_to_one")
    periods = {"validation": (folds[1][0], folds[3][-1]), "assessment": (folds[4][0], sessions[-1])}
    results, metadata, predictions = {}, {}, {}
    context = add_context(frame, bars, sessions, statistics)
    cutoff = float(context[context.trade_date < periods["validation"][0]].drop_duplicates("trade_date").market_volatility_20d.median())
    for name in FORMULAS:
        prediction = scored[["symbol", "trade_date", "fold", name]].rename(columns={name: "score"})
        predictions[name] = prediction
        older = prediction[prediction.fold <= 3]
        results[name] = {"validation": evaluate(older, statistics, bars, sessions, events, *periods["validation"], args.output / f"{name}_validation.json")}
        metadata[name] = {"features": "equation", "target": "execution", "fold_quality":
                          [quality(older[older.fold == f], frame, "execution", periods["validation"][1]) for f in (1, 2, 3)]}
        print(f"QUANT validation {name}", flush=True)
    winner, rejected = choose_paper_candidate({n: r["validation"] for n, r in results.items()}, metadata)
    atomic_json(args.output / "selection.json", {"selected_on_validation": winner, "rejection_reasons": rejected, "assessment_used": False})
    print(f"QUANT selection locked: {winner}", flush=True)
    robustness, checks = {}, {}
    for name, prediction in predictions.items():
        later = prediction[prediction.fold >= 4]
        later.to_parquet(args.output / f"{name}_assessment_signals.parquet", index=False)
        result = evaluate(later, statistics, bars, sessions, events, *periods["assessment"], args.output / f"{name}_assessment.json")
        results[name]["assessment"] = result
        checks[name] = adjusted_pair_check(result)
        detailed = json.loads((args.output / f"{name}_assessment.json").read_text())
        robustness[name] = {n: portfolio_robustness(detailed["retail"][n]["base"], context, cutoff) for n in ("candidate", "matched_momentum")}
        print(f"QUANT assessment {name}", flush=True)
    # Reference performance on precisely the equation pool, not a larger set.
    current = original.merge(scored[["symbol", "trade_date"]], on=["symbol", "trade_date"], validate="one_to_one")
    results["current_model_reference"] = {p: evaluate(current[current.fold.le(3) if p == "validation" else current.fold.ge(4)], statistics, bars, sessions, events, *boundaries,
                                          args.output / f"current_model_reference_{p}.json") for p, boundaries in periods.items()}
    report = {"status": "historical_research", "production_changed": False, "promotion_allowed": False,
              "protocol": protocol, "periods": periods, "selected_on_validation": winner, "validation_rejection_reasons": rejected,
              "results": results, "assessment_checks": checks, "robustness": robustness,
              "coverage": {"signal_rows": len(scored), "signal_dates": scored.trade_date.nunique(), "symbols": scored.symbol.nunique(),
                           "original_reference_rows": len(original), "retained_fraction": len(scored) / len(original)},
              "limits": ["Already researched historical period; six further tests add multiple-testing risk.",
                         "Provider-adjusted execution prices, current-universe/industry taxonomy and unresolved corporate entitlements remain.",
                         "These are fixed ranking equations, not calibrated profit probabilities or guaranteed price forecasts.",
                         "The residual signal uses a single market factor, not a reproduction of a published multifactor strategy.",
                         "No production or existing frozen paper-trial model is changed."]}
    atomic_json(args.output / "report.json", report)
    pct = lambda x: "unavailable" if x is None else f"{100*x:.2f}%"
    lines = ["# Fixed quant equations — historical research", "", f"Validation-only choice: {winner or 'none qualified'}. No promotion.", "",
             "| Formula | Validation net | Later net | Matched momentum | Later drawdown | Doubled costs | Delayed entry |", "|---|---:|---:|---:|---:|---:|---:|"]
    for name, r in results.items():
        v, a = r["validation"]["retail"], r["assessment"]["retail"]
        lines.append(f"| {name} | {pct(v['base']['net_return'])} | {pct(a['base']['net_return'])} | {pct(r['assessment']['matched_momentum_retail']['base']['net_return'])} | {pct(a['base']['max_drawdown'])} | {pct(a['double_costs']['net_return'])} | {pct(a['entry_delayed']['net_return'])} |")
    lines += [""] + [f"- {limit}" for limit in report["limits"]]
    (args.output / "summary.md").write_text("\n".join(lines) + "\n")
    print(f"QUANT COMPLETE: {args.output / 'summary.md'}", flush=True)


if __name__ == "__main__":
    main()
