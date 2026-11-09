"""Fixed 21-session, non-overlapping model/momentum/equal-weight experiment.

Read-only historical research. No tuning, retraining or production promotion.
All portfolios use the same equal-target allocator and execution constraints.
"""

import argparse
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timedelta
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "spark/jobs")]
from ml.evaluation.corporate_actions import load_events, fingerprint, crossing_events
from ml.evaluation.forecast_outcomes import feature_event_digest, outcome as stock_outcome
from ml.evaluation.forward_tracking import atomic_json, IST
from ml.evaluation.retail import Policy, eligibility, number

STRATEGIES = ("model", "momentum", "equal_weight")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def candidates(predictions, statistics, day, policy, events):
    """One signal-time pool for every strategy; no future outcomes filter it."""
    pool = []
    for prediction in predictions:
        row = {**statistics.get((prediction["symbol"], day), {}), **prediction,
               "trade_date": day, "rank": 1}
        if number(row.get("score")) is None or number(row.get("momentum")) is None:
            continue
        if not eligibility(row, policy, day, events):
            pool.append(row)
    return pool


def select(pool, strategy):
    if strategy == "equal_weight":
        return sorted(pool, key=lambda r: r["symbol"])
    field = {"model": "score", "momentum": "momentum"}[strategy]
    return sorted(pool, key=lambda r: (-r[field], r["symbol"]))[:20]


def allocate(rows, bars, day, capital, policy):
    """Common equal targets, scaled uniformly by stock/sector/risk/cash caps.

Missing entry prices invalidate the basket, rather than selecting replacement
stocks with hindsight. Whole shares may leave small residual cash.
"""
    if not rows:
        return {}, capital, None
    if len(rows) > policy.max_positions:
        raise ValueError("Basket exceeds common position limit")
    fee, slip = policy.fee_bps / 10000, policy.slippage_bps / 10000
    counts = Counter(r.get("industry", "Unknown") for r in rows)
    # Each stock starts with the same fraction of capital. Uniform scaling
    # preserves equal targets if a concentration or per-stock risk cap binds.
    risk_fraction = (1 + slip) * (1 + fee) - (1 - policy.stop_pct / 100) * (1 - slip) * (1 - fee)
    weight = min(1 / (len(rows) * (1 + slip) * (1 + fee)),
                 policy.max_position_pct / 100 / (1 + slip),
                 policy.max_sector_pct / 100 / max(counts.values()) / (1 + slip),
                 policy.risk_pct / 100 / risk_fraction)
    positions, cash = {}, capital
    for row in rows:
        symbol = row["symbol"]
        close = number(bars.get((symbol, day), {}).get("close"))
        if close is None or close <= 0:
            return {}, capital, {"date": day, "symbol": symbol, "reason": "Missing selected entry price"}
        entry = close * (1 + slip)
        quantity = min(math.floor(max(0, capital * weight - policy.flat_fee) / close),
                       math.floor(row["volume_avg_20d"] * policy.max_participation))
        if quantity <= 0:
            continue
        cost = quantity * entry * (1 + fee) + policy.flat_fee
        # Floating point and flat fees must never overspend remaining cash.
        quantity = min(quantity, math.floor(max(0, cash - policy.flat_fee) / (entry * (1 + fee))))
        if quantity <= 0:
            continue
        cost = quantity * entry * (1 + fee) + policy.flat_fee
        cash -= cost
        positions[symbol] = {"quantity": quantity, "entry": entry, "cost": cost,
                             "stop": close * (1 - policy.stop_pct / 100)}
    assert cash >= -1e-6
    return positions, cash, None


def cycle(rows, bars, sessions, entry_index, policy, capital, events):
    end = entry_index + policy.horizon
    entry_day = sessions[entry_index]
    positions, cash, blocked = allocate(rows, bars, entry_day, capital, policy)
    if blocked:
        return {"status": "incomplete", "blocked": blocked, "curve": [], "trades": []}
    initial_exposure = sum(p["quantity"] * p["entry"] for p in positions.values()) / capital
    allocated_count = len(positions)
    curve, trades = [], []
    for index in range(entry_index, end + 1):
        day = sessions[index]
        for symbol, position in list(positions.items()):
            if crossing_events(symbol, entry_day, day, events):
                blocked = {"date": day, "symbol": symbol, "reason": "Unvalued corporate-action entitlement"}
                break
            bar = bars.get((symbol, day), {})
            close = number(bar.get("close"))
            if close is None or close <= 0:
                blocked = {"date": day, "symbol": symbol, "reason": "Missing held-stock close"}
                break
            price, reason = close, "time"
            if index > entry_index:
                opening, low = number(bar.get("open")), number(bar.get("low"))
                if opening is None or low is None or min(opening, low) <= 0:
                    blocked = {"date": day, "symbol": symbol, "reason": "Missing stop OHLC"}
                    break
                if low <= position["stop"]:
                    price, reason = min(opening, position["stop"]), "stop"
                elif index != end:
                    continue
            elif index != end:
                continue
            fill = price * (1 - policy.slippage_bps / 10000)
            proceeds = position["quantity"] * fill * (1 - policy.fee_bps / 10000) - policy.flat_fee
            cash += proceeds
            trades.append({"symbol": symbol, "entry_date": entry_day, "exit_date": day,
                           **position, "exit_price": fill, "proceeds": proceeds,
                           "pnl": proceeds - position["cost"], "exit_reason": reason})
            del positions[symbol]
        if blocked:
            break
        equity = cash + sum(p["quantity"] * bars[(s, day)]["close"] for s, p in positions.items())
        curve.append({"date": day, "equity": equity, "cash": cash, "positions": len(positions)})
    return {"status": "incomplete" if blocked else "complete", "blocked": blocked,
            "curve": curve, "trades": trades, "entry_exposure": initial_exposure,
            "allocated_count": allocated_count,
            "net_return": None if blocked else cash / capital - 1}


def compare_period(signals, statistics, bars, sessions, policy, events, start, end):
    eligible_dates = [i for i, d in enumerate(sessions) if start <= d <= end and
                      i > 0 and sessions[i - 1] in signals and
                      i + policy.horizon < len(sessions) and sessions[i + policy.horizon] <= end]
    # Precommit one schedule shared by all strategies. Never omit a bad cycle
    # based on its outcome, or choose an offset after inspecting returns.
    schedule, next_index = [], -1
    for i in eligible_dates:
        if i >= next_index:
            schedule.append(i)
            next_index = i + policy.horizon + 1
    results = {name: {"status": "complete", "capital": policy.capital, "curve": [],
                      "trades": [], "cycles": [], "blocked": None} for name in STRATEGIES}
    shadow = {name: [] for name in STRATEGIES}
    for i in schedule:
        signal = sessions[i - 1]
        pool = candidates(signals[signal], statistics, signal, policy, events)
        # Equal-notional shadow isolates selection from indivisible shares,
        # industry/risk caps and cash deployment. It is not an executable book.
        outcomes = {r["symbol"]: stock_outcome(r["symbol"], i, policy.horizon, bars, sessions, events, policy)
                    for r in pool}
        for name, result in results.items():
            selected = select(pool, name)
            selected_outcomes = [outcomes[r["symbol"]] for r in selected]
            missing = Counter(r["status"] for r in selected_outcomes if r["status"] != "complete")
            shadow[name].append({"signal_date": signal, "selected_count": len(selected),
                                 "status": "incomplete" if missing else "complete", "missing": dict(missing),
                                 "net_return": None if missing else (sum(r["net_return"] for r in selected_outcomes) / len(selected) if selected else 0.)})
            if result["status"] != "complete":
                continue
            rows = selected
            outcome = cycle(rows, bars, sessions, i, policy, result["capital"], events)
            result["cycles"].append({"signal_date": signal, "entry_date": sessions[i],
                                     "scheduled_exit_date": sessions[i + policy.horizon],
                                     "eligible_count": len(pool), "selected_count": len(rows),
                                     "selected_symbols": [r["symbol"] for r in rows],
                                     **{k: outcome.get(k) for k in ("status", "blocked", "entry_exposure", "allocated_count", "net_return")}})
            result["curve"].extend(outcome["curve"])
            result["trades"].extend(outcome["trades"])
            if outcome["status"] != "complete":
                result.update(status="incomplete", blocked=outcome["blocked"])
            else:
                result["capital"] = outcome["curve"][-1]["equity"]
    for result in results.values():
        complete = result["status"] == "complete" and bool(schedule)
        if not schedule:
            result["status"] = "no_mature_cycles"
        peak, drawdown = policy.capital, 0.
        for point in result["curve"]:
            peak = max(peak, point["equity"])
            drawdown = min(drawdown, point["equity"] / peak - 1)
        result["net_return"] = result["capital"] / policy.capital - 1 if complete else None
        result["max_drawdown"] = drawdown if complete else None
        result["closed_trades"] = len(result["trades"])
        result["trade_win_rate"] = (sum(t["pnl"] > 0 for t in result["trades"]) / len(result["trades"])) if result["trades"] else None
        result["turnover_multiple"] = sum(t["cost"] + t["proceeds"] for t in result["trades"]) / policy.capital
        exposures = [c["entry_exposure"] for c in result["cycles"] if c["entry_exposure"] is not None]
        result["mean_entry_exposure"] = sum(exposures) / len(exposures) if exposures else None
    paired = {}
    for baseline in ("momentum", "equal_weight"):
        pairs = [(a["net_return"], b["net_return"]) for a, b in zip(results["model"]["cycles"], results[baseline]["cycles"])
                 if a["status"] == b["status"] == "complete"]
        differences = [a - b for a, b in pairs]
        # A descriptive paired interval, not a claim that market regimes are iid.
        import numpy as np
        rng = np.random.default_rng(42)
        interval = list(map(float, np.quantile([np.mean(rng.choice(differences, len(differences), replace=True))
                                               for _ in range(2000)], [.025, .975]))) if differences else None
        paired[baseline] = {"paired_cycles": len(pairs), "model_cycle_win_rate": sum(d > 0 for d in differences) / len(pairs) if pairs else None,
                            "mean_cycle_difference": sum(differences) / len(pairs) if pairs else None,
                            "descriptive_95pct_interval": interval,
                            "full_period_comparable": results["model"]["status"] == results[baseline]["status"] == "complete",
                            "interval_method": "Paired non-overlapping cycle bootstrap, 2000 samples, seed 42; regime dependence remains"}
    shadow_results = {}
    for name, cycles in shadow.items():
        complete = bool(cycles) and all(c["status"] == "complete" for c in cycles)
        shadow_results[name] = {"status": "complete" if complete else "incomplete",
                                "net_return": math.prod(1 + c["net_return"] for c in cycles) - 1 if complete else None,
                                "complete_cycles": sum(c["status"] == "complete" for c in cycles), "cycles": cycles}
    shadow_pairs = {}
    for name in ("momentum", "equal_weight"):
        differences = [a["net_return"] - b["net_return"] for a, b in zip(shadow["model"], shadow[name])
                       if a["status"] == b["status"] == "complete"]
        rng = np.random.default_rng(42)
        interval = list(map(float, np.quantile([np.mean(rng.choice(differences, len(differences), replace=True))
                                               for _ in range(2000)], [.025, .975]))) if differences else None
        shadow_pairs[name] = {"paired_cycles": len(differences),
                              "mean_cycle_difference": sum(differences) / len(differences) if differences else None,
                              "descriptive_95pct_interval": interval,
                              "full_period_comparable": shadow_results["model"]["status"] == shadow_results[name]["status"] == "complete"}
    return {"strategies": results, "paired_comparisons": paired, "scheduled_cycles": len(schedule),
            "selection_diagnostic": {"strategies": shadow_results, "paired_comparisons": shadow_pairs,
                                     "method": "Fractional equal-notional baskets with identical stop/cost rules; excludes capital, whole-share, participation, industry and sizing constraints. Not an executable retail portfolio."}}


def main():
    import pandas as pd
    from pyspark.sql import SparkSession, functions as F
    from daily_history import latest_bars
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--research", type=Path, default=ROOT / "ml/evaluation/results/retail_research_20261108")
    parser.add_argument("--output", type=Path, default=ROOT / "ml/evaluation/results/baseline_comparison_20261108")
    parser.add_argument("--warehouse", default="hdfs://localhost:9000/warehouse")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    config = json.loads((args.research / "config.json").read_text())
    events = load_events()
    archived = json.loads((args.research / "source_snapshots/config/corporate_actions.json").read_text())["events"]
    if feature_event_digest(events) != feature_event_digest(archived):
        raise ValueError("Prediction feature windows no longer match")
    frames, hashes = [], {}
    for fold in range(1, 6):
        path = args.research / f"rank21_fold{fold}.csv"
        frame = pd.read_csv(path, dtype={"trade_date": str})
        frame["fold"] = fold
        frames.append(frame)
        hashes[path.name] = digest(path)
    predictions = pd.concat(frames, ignore_index=True)
    if predictions.duplicated(["symbol", "trade_date"]).any():
        raise ValueError("Duplicate prediction keys")
    earliest = predictions.trade_date.min()
    start = (datetime.fromisoformat(earliest) - timedelta(days=220)).date().isoformat()
    spark = (SparkSession.builder.appName("controlled_baseline_comparison")
             .config("spark.sql.catalog.iceberg", "org.apache.iceberg.spark.SparkCatalog")
             .config("spark.sql.catalog.iceberg.type", "hadoop")
             .config("spark.sql.catalog.iceberg.warehouse", args.warehouse).getOrCreate())
    spark.sparkContext.setLogLevel("ERROR")
    try:
        snapshot = str(spark.sql("SELECT snapshot_id FROM iceberg.bronze.quotes_daily.snapshots ORDER BY committed_at DESC LIMIT 1").first().snapshot_id)
        if snapshot != config["source_snapshots"]["bronze.quotes_daily"]:
            raise ValueError("Price snapshot differs from prediction research")
        raw = latest_bars(spark.table("iceberg.bronze.quotes_daily")).filter(
            (F.col("trade_date") >= F.lit(start).cast("date")) &
            (F.col("trade_date") < F.lit(datetime.now(IST).date().isoformat()).cast("date")))
        bars = {}
        for row in raw.select("symbol", "trade_date", "open", "low", "close", "volume").toLocalIterator():
            bars[(row.symbol, str(row.trade_date))] = {k: number(row[k]) for k in ("open", "low", "close", "volume")}
        print(f"COMPARISON loaded {len(bars)} adjusted daily bars", flush=True)
    finally:
        spark.stop()
    sessions = sorted(d for s, d in bars if s == "^NSEI")
    prices = pd.DataFrame([{"symbol": s, "trade_date": d, **b} for (s, d), b in bars.items()]).sort_values(["symbol", "trade_date"])
    grouped = prices.groupby("symbol")
    prices["volume_avg_20d"] = grouped.volume.transform(lambda v: v.rolling(20, min_periods=20).mean())
    prices["ret"] = grouped.close.pct_change(fill_method=None)
    prices["volatility_20d"] = prices.groupby("symbol").ret.transform(lambda v: v.rolling(20, min_periods=20).std())
    prices["momentum"] = grouped.close.pct_change(63, fill_method=None)
    # Require the exact common-calendar lookback, rather than 63 available bars
    # across a suspension. No future observation enters the momentum signal.
    expected = {day: sessions[i - 63] if i >= 63 else None for i, day in enumerate(sessions)}
    prices["lookback_date"] = grouped.trade_date.shift(63)
    prices.loc[prices.lookback_date != prices.trade_date.map(expected), "momentum"] = float("nan")
    industries = {s["yf_symbol"]: s["industry"] for s in json.loads((ROOT / "poller/universe/universe_cache.json").read_text())["stocks"]}
    prices["industry"] = prices.symbol.map(industries).fillna("Unknown")
    stats = {(r["symbol"], r["trade_date"]): {k: (None if pd.isna(v) else v) for k, v in r.items()}
             for r in prices.to_dict("records")}
    signals = {day: group.to_dict("records") for day, group in predictions.groupby("trade_date")}
    policy = Policy(max_positions=1000)
    boundaries = {"validation": (predictions[predictions.fold == 1].trade_date.min(), predictions[predictions.fold == 3].trade_date.max()),
                  "assessment": (predictions[predictions.fold == 4].trade_date.min(), sessions[-1])}
    manifest = {"price_snapshot": snapshot, "prediction_files": hashes, "events_digest": fingerprint(events),
                "research_directory": str(args.research.resolve()),
                "research_config_sha256": digest(args.research / "config.json"),
                "universe_sha256": digest(ROOT / "poller/universe/universe_cache.json"),
                "policy": asdict(policy), "periods": boundaries,
                "source_hashes": {name: digest(ROOT / "ml/evaluation" / name) for name in
                                  ("compare_baselines.py", "retail.py", "corporate_actions.py", "forecast_outcomes.py")},
                "momentum": "63-session close return; no optimization or skip-month tuning",
                "schedule": "First mature entry per period; next entry after prior scheduled exit; no offset search"}
    existing = args.output / "config.json"
    if existing.exists() and json.loads(existing.read_text()) != json.loads(json.dumps(manifest)):
        raise ValueError("Output has a different protocol; choose a new output directory")
    atomic_json(existing, manifest)
    results = {}
    for period, (begin, end) in boundaries.items():
        result = compare_period(signals, stats, bars, sessions, policy, events, begin, end)
        atomic_json(args.output / f"{period}.json", result)
        results[period] = {"scheduled_cycles": result["scheduled_cycles"], "paired_comparisons": result["paired_comparisons"],
                           "selection_diagnostic": result["selection_diagnostic"],
                           "strategies": {name: {k: value for k, value in r.items() if k not in ("curve", "trades")}
                                          for name, r in result["strategies"].items()}}
        print(f"COMPARISON {period}: {result['scheduled_cycles']} scheduled cycles", flush=True)
    report = {"status": "provisional", "production_changed": False, "promotion_allowed": False,
              "config": manifest, "results": results,
              "limitations": ["Previously researched history, not a fresh holdout; no strategy selected or promoted.",
                              "Provider-adjusted OHLC is not verified executable raw pricing; corporate-action gaps remain explicit.",
                              "Shared pool requires an available out-of-sample model score; historical universe/financials retain survivorship and restatement bias.",
                              "Equal targets use whole shares and common risk, industry, liquidity and cash caps; realized weights and cash exposure differ after rounding. No fractional-share or index-fund equivalence is claimed.",
                              "Common 1000-position research ceiling permits the all-eligible baseline; this is not the dashboard ten-position portfolio.",
                              "Non-overlapping cycles reduce overlap but do not remove market-regime dependence; paired intervals are descriptive.",
                              "No daily re-entry after stops, exchange limit fills, investor taxes or cash interest modeled."]}
    atomic_json(args.output / "report.json", report)
    pct = lambda v: "unavailable" if v is None else f"{v * 100:.2f}%"
    lines = ["# Controlled 21-session baseline comparison", "", "Research only. Common equal-target allocation, next-close entry, 8% stop, 10 bps fees + 10 bps slippage per side."]
    for period, result in results.items():
        lines += ["", f"## {period}", "", f"{boundaries[period][0]} to {boundaries[period][1]}; {result['scheduled_cycles']} pre-scheduled, non-overlapping cycles.", "",
                  "| Strategy | Status | Net return | Max drawdown | Mean entry exposure | Closed trades |", "|---|---|---:|---:|---:|---:|"]
        for name, r in result["strategies"].items():
            lines.append(f"| {name} | {r['status']} | {pct(r['net_return'])} | {pct(r['max_drawdown'])} | {pct(r['mean_entry_exposure'])} | {r['closed_trades']} |")
        lines += [""]
        for name, pair in result["paired_comparisons"].items():
            interval = pair["descriptive_95pct_interval"]
            interval_text = f"{pct(interval[0])} to {pct(interval[1])}" if interval else "unavailable"
            lines.append(f"Model minus {name}: mean cycle difference {pct(pair['mean_cycle_difference'])}; paired cycles {pair['paired_cycles']}; descriptive 95% interval {interval_text}; full-period comparable: {pair['full_period_comparable']}.")
        diagnostic = result["selection_diagnostic"]
        lines += ["", "### Equal-notional selection diagnostic", "", diagnostic["method"], "",
                  "| Strategy | Status | Net return | Complete cycles |", "|---|---|---:|---:|"]
        for name, r in diagnostic["strategies"].items():
            lines.append(f"| {name} | {r['status']} | {pct(r['net_return'])} | {r['complete_cycles']} |")
        lines += [""]
        for name, pair in diagnostic["paired_comparisons"].items():
            interval = pair["descriptive_95pct_interval"]
            interval_text = f"{pct(interval[0])} to {pct(interval[1])}" if interval else "unavailable"
            lines.append(f"Equal-notional model minus {name}: mean cycle difference {pct(pair['mean_cycle_difference'])}; descriptive 95% interval {interval_text}; paired cycles {pair['paired_cycles']}; full-period comparable: {pair['full_period_comparable']}.")
    lines += [""] + [f"- {limit}" for limit in report["limitations"]]
    (args.output / "summary.md").write_text("\n".join(lines) + "\n")
    print(f"COMPARISON COMPLETE: {args.output / 'summary.md'}", flush=True)


if __name__ == "__main__":
    main()
