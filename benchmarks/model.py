"""
Experiment 3 — model performance vs the rule baseline (roadmap §4).

Consumes the Phase 1 evaluation stack output (ml/evaluation/results/comparison.json
+ ml/models/selected.json) and records a compact, reproducible artifact under
benchmarks/model/. With --run it first rebuilds/retrains/re-evaluates via
scripts/retrain.sh.

    python benchmarks/model.py            # from existing evaluation results
    python benchmarks/model.py --run      # fresh retrain + evaluation
"""

import argparse
import json
import os
import subprocess

from common import REPO_ROOT, write_json, write_text

COMPARISON = os.path.join(REPO_ROOT, "ml", "evaluation", "results", "comparison.json")
SELECTED = os.path.join(REPO_ROOT, "ml", "models", "selected.json")


def main():
    parser = argparse.ArgumentParser(description="Model performance experiment")
    parser.add_argument("--run", action="store_true", help="Run scripts/retrain.sh first")
    args = parser.parse_args()

    if args.run:
        subprocess.run(["bash", os.path.join(REPO_ROOT, "scripts", "retrain.sh")],
                       cwd=REPO_ROOT, check=True)

    if not os.path.exists(COMPARISON):
        raise SystemExit(f"{COMPARISON} not found — run with --run or evaluate first.")

    with open(COMPARISON) as fh:
        comparison = json.load(fh)
    selected = json.load(open(SELECTED)) if os.path.exists(SELECTED) else {"labels": {}}

    summary = {"evaluated_at": comparison.get("evaluated_at"),
               "k": comparison.get("k"), "cost_bps": comparison.get("cost_bps"),
               "labels": {}}
    lines = ["# Model performance", "",
             f"Evaluated {comparison.get('evaluated_at')} · K={comparison.get('k')} · "
             f"cost={comparison.get('cost_bps')} bps/side", ""]

    for label, models in comparison["labels"].items():
        rows = []
        lines += [f"## {label}", "",
                  "| model | IC | IC t | sec-neut IC | net Sharpe | net ann ret | max DD | turnover |",
                  "|---|---|---|---|---|---|---|---|"]
        for name, metrics in models.items():
            if name in ("walk_forward", "purged_kfold"):
                continue
            bt = metrics.get("backtest") or {}
            row = {
                "ic_mean": metrics.get("ic_mean"),
                "ic_t_stat": metrics.get("ic_t_stat"),
                "sector_neutral_ic_mean": metrics.get("sector_neutral_ic_mean"),
                "net_sharpe": bt.get("net_sharpe"),
                "net_annualized_return": bt.get("net_annualized_return"),
                "net_max_drawdown": bt.get("net_max_drawdown"),
                "avg_turnover": bt.get("avg_turnover"),
                "deflated_sharpe": bt.get("deflated_sharpe"),
            }
            rows.append({"model": name, **row})
            lines.append(
                f"| {name} | {_f(row['ic_mean'])} | {_f(row['ic_t_stat'])} | "
                f"{_f(row['sector_neutral_ic_mean'])} | {_f(row['net_sharpe'])} | "
                f"{_f(row['net_annualized_return'])} | {_f(row['net_max_drawdown'])} | "
                f"{_f(row['avg_turnover'])} |"
            )
        summary["labels"][label] = {
            "selected": selected.get("labels", {}).get(label),
            "walk_forward": models.get("walk_forward"),
            "purged_kfold": models.get("purged_kfold"),
            "models": rows,
        }
        lines.append("")

    payload = {
        "evaluated_at": summary["evaluated_at"],
        "k": summary["k"],
        "cost_bps": summary["cost_bps"],
        "n_trials": comparison.get("n_trials"),
        "summary": summary,
    }
    print(write_json("model", "model.json", payload))
    print(write_text("model", "summary.md", "\n".join(lines) + "\n"))


def _f(value):
    if value is None:
        return ""
    try:
        return f"{float(value):.4f}"
    except (TypeError, ValueError):
        return str(value)


if __name__ == "__main__":
    main()
