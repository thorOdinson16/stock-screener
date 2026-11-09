"""Conservative robustness gate and explicit plan-completion evidence."""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "ml/feature_engineering")]


def advancement_checks(result, robustness, fold_ic):
    reasons = []
    if sum(v is not None and v > 0 for v in fold_ic) < 2:
        reasons.append("Positive validation net-return rank IC in fewer than two folds")
    for scenario in ("base", "double_costs", "entry_delayed"):
        a, b = result["retail"][scenario], result["matched_momentum_retail"][scenario]
        if a["status"] != "complete" or b["status"] != "complete":
            reasons.append(f"Incomplete later {scenario} comparison")
        elif a["net_return"] <= max(0., b["net_return"]):
            reasons.append(f"No positive later advantage over matched momentum under {scenario}")
    base = result["retail"]["base"]
    if base["closed_trades"] < 20 or base["max_drawdown"] is None or base["max_drawdown"] < -.25:
        reasons.append("Later trade-count or drawdown requirement failed")
    if robustness.get("positive_pnl_top_stock_share") is not None and robustness["positive_pnl_top_stock_share"] > .5:
        reasons.append("One stock contributes more than half of positive closed-trade PnL")
    quarters = [q for q in robustness.get("quarters", {}).values() if q["dates"] >= 20]
    if quarters and sum(q["return"] > 0 for q in quarters) * 2 < len(quarters):
        reasons.append("Fewer than half of adequately observed later quarters are profitable")
    diagnostic = result["selection_diagnostic"]["strategies"]
    a, b = diagnostic["model"], diagnostic["momentum"]
    if a["status"] != "complete" or b["status"] != "complete":
        reasons.append("Equal-notional selection comparison is incomplete")
    elif a["net_return"] <= b["net_return"]:
        reasons.append("Equal-notional selection does not improve over momentum")
    pair = result["selection_diagnostic"]["paired_comparisons"]["momentum"]
    interval = pair["descriptive_95pct_interval"]
    if pair["paired_cycles"] < 8 or interval is None or interval[0] <= 0:
        reasons.append("Independent-cycle advantage is not established")
    return {"passed": not reasons, "reasons": reasons, "production_ready": False}


def main():
    import pandas as pd
    from ml.evaluation.model_tournament import prepare, sha, evaluate
    from ml.evaluation.research_context import portfolio_robustness, add_context
    from ml.evaluation.corporate_actions import load_events
    from ml.evaluation.forward_tracking import atomic_json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--research", type=Path, default=ROOT / "ml/evaluation/results/remaining_plan_20261108/run")
    parser.add_argument("--input", type=Path, default=ROOT / "ml/evaluation/results/tournament_inputs_20261108")
    parser.add_argument("--base", type=Path, default=ROOT / "ml/evaluation/results/model_tournament_20261108")
    args = parser.parse_args()
    policy = {"source_sha256": sha(Path(__file__)), "positive_validation_ic_folds": 2, "minimum_closed_trades": 20,
              "minimum_independent_cycles": 8, "maximum_drawdown": -.25, "maximum_top_stock_positive_pnl_share": .5,
              "quarter_rule": "At least half of later quarters with >=20 valued dates must be profitable",
              "advantage_rule": "Positive returns beating matched momentum under all three scenarios, equal-notional improvement, and a positive lower paired-cycle interval",
              "production_ready": False}
    protocol = args.research / "audit_protocol.json"
    if protocol.exists() and json.loads(protocol.read_text()) != policy:
        raise RuntimeError("Audit policy changed; version the audit separately")
    atomic_json(protocol, policy)
    report = json.loads((args.research / "report.json").read_text())
    frame, bars, sessions, statistics, _ = prepare(args.input, args.base)
    context = add_context(frame, bars, sessions, statistics)
    cutoff = float(context[context.trade_date < report["periods"]["validation"][0]].drop_duplicates("trade_date").market_volatility_20d.median())
    checks, validation_robustness = {}, {}
    for name, result in report["results"].items():
        parts = [pd.read_parquet(args.research / "predictions" / f"{name}_fold{f}.parquet") for f in (1, 2, 3)]
        fold_ic = []
        for prediction in parts:
            merged = prediction.merge(frame[["symbol", "trade_date", "net_return", "scheduled_exit_date"]], on=["symbol", "trade_date"], validate="one_to_one")
            merged = merged[merged.net_return.notna() & (merged.scheduled_exit_date <= report["periods"]["validation"][1])]
            values = [p.score.corr(p.net_return, method="spearman") for _, p in merged.groupby("trade_date") if p.score.nunique() > 1 and p.net_return.nunique() > 1]
            fold_ic.append(float(pd.Series(values).mean()) if values else None)
        checks[name] = {**advancement_checks(result["assessment"], report["robustness"][name]["candidate"], fold_ic), "validation_fold_net_rank_ic": fold_ic}
        detailed = json.loads((args.research / f"{name}_validation.json").read_text())
        validation_robustness[name] = portfolio_robustness(detailed["retail"]["candidate"]["base"], context, cutoff)
    # A complementary ensemble is conditional on individually robust members.
    # Do not manufacture one after every individual fails these checks.
    qualified = [n for n, c in checks.items() if c["passed"]]
    ensemble = {"status": "not_tested_no_two_qualified_members" if len(qualified) < 2 else "requires_separately_frozen_validation_only_ensemble_experiment",
                "qualified_members": qualified, "production_changed": False}
    financial = report["verified_financial_experiment"]
    financial_later = {}
    if financial["status"] == "historical_subset_tested":
        for name in ("matched_context_control", "verified_financials"):
            prediction = pd.read_parquet(args.research / "predictions" / f"{name}.parquet")
            later = prediction[prediction.fold == prediction.fold.max()]
            start = later.trade_date.min()
            end_i = min(len(sessions) - 1, sessions.index(later.trade_date.max()) + 22)
            financial_later[name] = evaluate(later, statistics, bars, sessions, load_events(), start, sessions[end_i], args.research / f"{name}_later_only.json")
    outcome = {"status": "complete", "source_report_sha256": sha(args.research / "report.json"),
               "checks": checks, "validation_robustness": validation_robustness, "ensemble": ensemble,
               "verified_financial_later_only": financial_later,
               "data_paths": {"input": str(args.input.resolve()), "base": str(args.base.resolve())},
               "fresh_outcomes": "Must mature after actual prospective publication; historical experiments cannot complete this step"}
    atomic_json(args.research / "completion_audit.json", outcome)
    print("ROBUSTNESS AUDIT COMPLETE; qualified historical members:", qualified)


if __name__ == "__main__":
    main()
