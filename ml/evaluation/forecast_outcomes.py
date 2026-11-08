"""Audit next-close/stop-based outcomes for existing walk-forward predictions.

Every attempted top-20 case is retained, including unavailable outcomes. Does not
rewrite source predictions, production models, Iceberg tables or live evidence.
"""

import argparse
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "spark/jobs"), str(ROOT / "ml/evaluation")]


def feature_event_digest(events):
    # Entitlement research can add evidence without changing the indicator/label
    # windows used to produce a historical prediction. Verify those separately.
    fields = ["id", "symbols", "impact_start", "impact_end"]
    return hashlib.sha256(json.dumps([{k: e[k] for k in fields} for e in events], sort_keys=True).encode()).hexdigest()


def outcome(symbol, entry_index, horizon, bars, sessions, events, policy):
    try:
        from .corporate_actions import crossing_events
    except ImportError:
        from corporate_actions import crossing_events
    scheduled_index = entry_index + horizon
    if scheduled_index >= len(sessions):
        return {"status": "immature", "reason": "Scheduled holding window has not matured"}
    entry_date, scheduled = sessions[entry_index], sessions[scheduled_index]
    result = {"entry_date": entry_date, "scheduled_exit_date": scheduled}
    entry = bars.get((symbol, entry_date), {}).get("close")
    if entry is None or entry <= 0:
        return {**result, "status": "missing_price", "reason": "Missing entry close"}
    stop = entry * (1 - policy.stop_pct / 100)
    exit_date, exit_price, reason = scheduled, None, "time"
    # Entering on a restructuring window is ambiguous in the adjusted series.
    if crossing_events(symbol, entry_date, entry_date, events):
        return {**result, "status": "corporate_action", "reason": "Entry during restructuring window"}
    for day in sessions[entry_index + 1:scheduled_index + 1]:
        if crossing_events(symbol, entry_date, day, events):
            return {**result, "status": "corporate_action", "reason": "Unvalued share entitlement before exit"}
        bar = bars.get((symbol, day), {})
        opening, low, close = (bar.get(k) for k in ("open", "low", "close"))
        if any(v is None or v <= 0 for v in (opening, low, close)):
            return {**result, "status": "missing_price", "reason": "Missing stop/valuation OHLC"}
        if low <= stop:
            exit_date, exit_price, reason = day, min(opening, stop), "stop"
            break
        exit_price = close
    fee, slip = policy.fee_bps / 10000, policy.slippage_bps / 10000
    net = exit_price * (1 - slip) * (1 - fee) / (entry * (1 + slip) * (1 + fee)) - 1
    return {**result, "status": "complete", "reason": "", "actual_exit_date": exit_date,
            "exit_reason": reason, "entry_close": entry, "exit_reference": exit_price,
            "net_return": net, "profit": int(net > 1e-12)}


def main():
    import pandas as pd
    from pyspark.sql import SparkSession, functions as F
    from daily_history import latest_bars
    from corporate_actions import load_events, adjusted_return, fingerprint
    from retail import Policy, eligibility
    from forward_tracking import IST, atomic_json

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--research", type=Path, default=ROOT / "ml/evaluation/results/retail_research_20261108")
    parser.add_argument("--output", type=Path, default=ROOT / "ml/evaluation/results/forecast_lab")
    parser.add_argument("--warehouse", default="hdfs://localhost:9000/warehouse")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    config = json.loads((args.research / "config.json").read_text())
    events = load_events()
    archived_events = json.loads((args.research / "source_snapshots/config/corporate_actions.json").read_text())["events"]
    if feature_event_digest(events) != feature_event_digest(archived_events):
        raise RuntimeError("Corporate-action feature windows changed; regenerate walk-forward predictions")
    predictions, hashes = {}, {}
    for horizon in (21, 63):
        parts = []
        for fold in range(1, 6):
            path = args.research / f"rank{horizon}_fold{fold}.csv"
            hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
            frame = pd.read_csv(path, dtype={"trade_date": str})
            frame = frame.sort_values(["trade_date", "score", "symbol"], ascending=[True, False, True])
            frame["rank"] = frame.groupby("trade_date").cumcount() + 1
            frame["universe_size"] = frame.groupby("trade_date")["symbol"].transform("size")
            frame["rank_percentile"] = 1 - (frame["rank"] - 1) / frame["universe_size"]
            frame["fold"] = fold
            parts.append(frame[frame["rank"] <= 20])
        predictions[horizon] = pd.concat(parts, ignore_index=True)
    earliest = min(f.trade_date.min() for f in predictions.values())
    start = (datetime.fromisoformat(earliest) - timedelta(days=60)).date().isoformat()
    today = datetime.now(IST).date().isoformat()
    spark = (SparkSession.builder.appName("forecast_outcome_audit")
             .config("spark.sql.catalog.iceberg", "org.apache.iceberg.spark.SparkCatalog")
             .config("spark.sql.catalog.iceberg.type", "hadoop")
             .config("spark.sql.catalog.iceberg.warehouse", args.warehouse).getOrCreate())
    spark.sparkContext.setLogLevel("ERROR")
    try:
        snapshot = str(spark.sql("SELECT snapshot_id FROM iceberg.bronze.quotes_daily.snapshots ORDER BY committed_at DESC LIMIT 1").first().snapshot_id)
        if snapshot != config["source_snapshots"]["bronze.quotes_daily"]:
            raise RuntimeError("Price snapshot differs from the prediction research; reproduce from the matching historical snapshot")
        raw = latest_bars(spark.table("iceberg.bronze.quotes_daily")).filter(
            (F.col("trade_date") >= F.lit(start).cast("date")) & (F.col("trade_date") < F.lit(today).cast("date")))
        rows = raw.select("symbol", "trade_date", "open", "low", "close", "volume").toLocalIterator()
        bars, daily_members = {}, {}
        for row in rows:
            day = str(row.trade_date)
            bars[(row.symbol, day)] = {key: float(row[key]) if row[key] is not None else None for key in ("open", "low", "close", "volume")}
            if not row.symbol.startswith("^"):
                daily_members.setdefault(day, []).append(row.symbol)
        sessions = sorted(day for symbol, day in bars if symbol == "^NSEI")
        positions = {day: i for i, day in enumerate(sessions)}
        prices = {key: row["close"] for key, row in bars.items() if row["close"] is not None and row["close"] > 0}
        price_frame = pd.DataFrame([{"symbol": s, "trade_date": d, "close": b["close"], "volume": b["volume"]} for (s, d), b in bars.items()])
        price_frame = price_frame.sort_values(["symbol", "trade_date"])
        price_frame["avg_volume"] = price_frame.groupby("symbol")["volume"].transform(lambda s: s.rolling(20, min_periods=20).mean())
        price_frame["ret"] = price_frame.groupby("symbol")["close"].pct_change(fill_method=None)
        price_frame["volatility"] = price_frame.groupby("symbol")["ret"].transform(lambda s: s.rolling(20, min_periods=20).std())
        stats = {(r.symbol, r.trade_date): (r.avg_volume, r.volatility) for r in price_frame.itertuples()}
        all_cases = []
        for horizon, frame in predictions.items():
            policy = Policy(horizon=horizon)
            benchmark_cache = {}
            for row in frame.itertuples():
                avg, volatility = stats.get((row.symbol, row.trade_date), (None, None))
                avg = None if avg is None or pd.isna(avg) else float(avg)
                volatility = None if volatility is None or pd.isna(volatility) else float(volatility)
                reference = prices.get((row.symbol, row.trade_date))
                candidate = {"symbol": row.symbol, "rank": row.rank, "trade_date": row.trade_date,
                             "close": reference, "volume_avg_20d": avg, "volatility_20d": volatility}
                reasons = eligibility(candidate, policy, row.trade_date, events)
                case = {"symbol": row.symbol, "signal_date": row.trade_date, "horizon": horizon,
                        "fold": row.fold, "rank": row.rank, "rank_percentile": row.rank_percentile,
                        "volatility": volatility, "eligible": not reasons, "eligibility_reasons": "; ".join(reasons)}
                entry_index = positions.get(row.trade_date)
                if entry_index is None:
                    result = {"status": "missing_calendar", "reason": "Signal date absent from benchmark calendar"}
                else:
                    result = outcome(row.symbol, entry_index + 1, horizon, bars, sessions, events, policy)
                case.update(result)
                if result["status"] == "complete":
                    key = row.trade_date
                    if key not in benchmark_cache:
                        returns = [adjusted_return(s, result["entry_date"], result["scheduled_exit_date"], prices, events)[0] for s in daily_members.get(key, [])]
                        benchmark_cache[key] = sum(returns) / len(returns) if returns and all(r is not None for r in returns) else None
                    case["universe_return"] = benchmark_cache[key]
                    case["outperform"] = int(case["net_return"] > case["universe_return"] + 1e-12) if case["universe_return"] is not None else None
                all_cases.append(case)
            print(f"AUDIT {horizon} sessions: {len(frame)} prospective-style top-20 cases", flush=True)
        output_frame = pd.DataFrame(all_cases)
        output_frame.to_csv(args.output / "outcomes.csv", index=False)
        metadata = {"created_at": datetime.now(IST).isoformat(), "price_snapshot": snapshot,
                    "last_completed_session": sessions[-1], "events_digest": fingerprint(events),
                    "feature_events_digest": feature_event_digest(events), "prediction_files": hashes,
                    "research_config": config, "policy": Policy().__dict__,
                    "status": "provisional", "price_basis": "provider_adjusted",
                    "algorithm_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                    "status_counts": {str(h): output_frame[output_frame.horizon == h].status.value_counts().to_dict() for h in (21, 63)},
                    "outcomes_sha256": hashlib.sha256((args.output / "outcomes.csv").read_bytes()).hexdigest()}
        atomic_json(args.output / "outcome_audit.json", metadata)
        print(f"AUDIT COMPLETE: {args.output / 'outcomes.csv'}", flush=True)
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
