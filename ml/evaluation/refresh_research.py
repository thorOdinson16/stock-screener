"""Incremental provider refresh into a separate paper-research snapshot only."""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "spark/jobs"), str(ROOT / "ml/feature_engineering"), str(ROOT / "poller")]


def main():
    import pandas as pd
    import yfinance as yf
    from indicators import compute_features
    from transform import transform_group
    from incremental import adjustment_revision
    from ml.evaluation.corporate_actions import load_events, reset_features, fingerprint
    from ml.evaluation.forward_tracking import atomic_json, IST
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "ml/evaluation/results/tournament_inputs_20261108")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--repair-revisions", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    if (args.output / "manifest.json").exists():
        raise RuntimeError("Refresh already frozen; use a new directory")
    old_manifest = json.loads((args.input / "manifest.json").read_text())
    old = pd.read_parquet(args.input / "bars.parquet")
    for name, expected in old_manifest["files"].items():
        if hashlib.sha256((args.input / name).read_bytes()).hexdigest() != expected:
            raise RuntimeError("Original research export changed")
    today = datetime.now(IST).date()
    symbols = sorted(old.symbol.unique())
    cache = args.output / "downloads"
    cache.mkdir(exist_ok=True)
    def fetch(symbol):
        previous = old[old.symbol == symbol].sort_values("trade_date")
        begin = (datetime.fromisoformat(previous.trade_date.max()).date() - timedelta(days=7)).isoformat()
        path = cache / (symbol.replace("/", "_") + ".json")
        if path.exists():
            saved = json.loads(path.read_text())
            if saved["status"] != "adjustment_revision" or not args.repair_revisions:
                return saved
            begin = previous.trade_date.min()
        result = {"symbol": symbol, "start": begin, "status": "error", "rows": []}
        try:
            data = yf.Ticker(symbol).history(start=begin, end=(today + timedelta(days=1)).isoformat(),
                    auto_adjust=True, repair=True, actions=True, timeout=10, raise_errors=True)
            rows = [{"symbol": symbol, "trade_date": idx.date().isoformat(),
                     **{k: float(row[v]) if pd.notna(row[v]) else None for k, v in
                        [("open", "Open"), ("low", "Low"), ("close", "Close"), ("volume", "Volume")]}}
                    for idx, row in data.iterrows() if idx.date() < today]
            reference = {"closes": previous.tail(20).set_index("trade_date").close.to_dict()}
            if not rows:
                result["reason"] = "No completed provider bars"
            elif adjustment_revision(rows, reference):
                # A frozen model was trained on the older price basis. Do not
                # silently mix revised history into that paper trial.
                result.update(status="adjustment_revision", reason="Historical price basis changed; separately reviewed rebuild required")
            else:
                result.update(status="complete", rows=rows, latest=max(r["trade_date"] for r in rows))
            if args.repair_revisions and path.exists() and json.loads(path.read_text())["status"] == "adjustment_revision" and len(rows) >= 252:
                result.update(status="complete", rows=rows, latest=max(r["trade_date"] for r in rows),
                              full_basis_rebuild=True, reason="Complete provider history replaced on one current adjustment basis; provisional execution prices")
        except Exception as error:
            result["reason"] = f"{type(error).__name__}: {str(error)[:300]}"
        atomic_json(path, result)
        return result
    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        for future in as_completed([executor.submit(fetch, symbol) for symbol in symbols]):
            results.append(future.result())
            if len(results) % 25 == 0:
                print(f"REFRESH {len(results)}/{len(symbols)}", flush=True)
    atomic_json(args.output / "retrieval.json", {"asof": today.isoformat(), "results": results})
    added = [r for result in results if result["status"] == "complete" for r in result["rows"]]
    rebuilt = {r["symbol"] for r in results if r.get("full_basis_rebuild") and r["status"] == "complete"}
    fresh = pd.concat([old[~old.symbol.isin(rebuilt)], pd.DataFrame(added)], ignore_index=True).drop_duplicates(["symbol", "trade_date"], keep="last")
    fresh = fresh.sort_values(["symbol", "trade_date"])
    index = fresh[fresh.symbol == "^NSEI"]
    latest = index.trade_date.max()
    updated = {r["symbol"] for r in results if r["status"] == "complete" and r["latest"] == latest}
    stocks = [s for s in symbols if not s.startswith("^")]
    coverage = len(set(stocks) & updated) / len(stocks)
    audit = {"status": "provisional", "latest": latest, "coverage": coverage,
             "ready_for_paper": (today - datetime.fromisoformat(latest).date()).days <= 5 and coverage >= .98,
             "refreshed_symbols": len(updated), "required_coverage": .98,
             "failed_symbols": [r["symbol"] for r in results if r["status"] != "complete"], "full_basis_rebuilds": sorted(rebuilt)}
    atomic_json(args.output / "refresh_audit.json", audit)
    if not audit["ready_for_paper"]:
        print("REFRESH incomplete; no paper-ready snapshot created", flush=True)
        return
    events = load_events()
    # Refuse selected symbols on an unreviewed revised basis. Partial refreshes
    # cannot introduce an older stock into the newest cross-section.
    safe = pd.concat([reset_features(group[["symbol", "trade_date", "close", "volume"]], events, compute_features)
                      for _, group in fresh.groupby("symbol")], ignore_index=True)
    normalized = pd.concat([transform_group(group) for _, group in safe.groupby("trade_date")], ignore_index=True)
    industries = {s["yf_symbol"]: s["industry"] for s in json.loads((ROOT / "poller/universe/universe_cache.json").read_text())["stocks"]}
    dataset = normalized.merge(safe[["symbol", "trade_date", "close"]], on=["symbol", "trade_date"], validate="one_to_one")
    dataset = dataset[~dataset.symbol.str.startswith("^")].copy()
    dataset["industry"] = dataset.symbol.map(industries).fillna("Unknown")
    fresh.to_parquet(args.output / "bars.parquet", index=False)
    dataset.to_parquet(args.output / "dataset.parquet", index=False)
    context_cols = ["symbol", "trade_date", "volume_avg_20d", "volatility_20d", "price_momentum_1m", "price_momentum_3m", "rsi_14"]
    safe[context_cols].to_parquet(args.output / "context.parquet", index=False)
    atomic_json(args.output / "events.json", {"version": 1, "events": events})
    sources = ["ml/evaluation/refresh_research.py", "spark/jobs/indicators.py", "ml/feature_engineering/transform.py",
               "ml/evaluation/corporate_actions.py", "poller/incremental.py", "poller/universe/universe_cache.json"]
    digest = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
    atomic_json(args.output / "manifest.json", {"status": "paper_only", "source_snapshots": old_manifest["source_snapshots"],
                "parent_manifest": digest(args.input / "manifest.json"), "retrieval_sha256": digest(args.output / "retrieval.json"),
                "source": "yfinance auto-adjusted incremental overlap; isolated from production warehouse",
                "last_session": latest, "events_digest": fingerprint(events), "created_at": datetime.now(IST).isoformat(),
                "files": {n: digest(args.output / n) for n in ["dataset.parquet", "bars.parquet", "context.parquet", "events.json"]},
                "source_hashes": {n: digest(ROOT / n) for n in sources}, "rows": len(dataset),
                "fundamentals_status": "Not used in frozen paper candidate", "refresh_coverage": coverage})
    print(f"REFRESH COMPLETE: {latest}; {coverage:.1%} latest-date coverage", flush=True)


if __name__ == "__main__":
    main()
