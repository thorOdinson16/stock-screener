"""Prospective, long-only pick cohorts. No dependency on Spark or model fitting."""

from collections import defaultdict
from datetime import datetime, timezone, timedelta
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
try:
    from .corporate_actions import load_events, adjusted_return, fingerprint
except ImportError:
    from corporate_actions import load_events, adjusted_return, fingerprint

IST = timezone(timedelta(hours=5, minutes=30))
HORIZONS = (5, 21)
DEFAULT_DIR = Path(__file__).resolve().parent / "results" / "forward"


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, allow_nan=False, indent=2))
    temporary.replace(path)


def capture(root, published_at, rows, model, now=None, cost_bps=10):
    """Freeze a fresh, served snapshot exactly once; never reconstruct old picks."""
    now = now or datetime.now(timezone.utc)
    published = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    age = (now - published).total_seconds()
    if age < -60 or age > 600:
        raise ValueError("Only fresh served snapshots can start prospective tracking")
    if not rows or not math.isfinite(cost_bps) or cost_bps < 0:
        raise ValueError("A nonempty snapshot and non-negative finite cost are required")
    snapshot_id = hashlib.sha256(published_at.encode()).hexdigest()[:24]
    path = Path(root) / "predictions" / f"{snapshot_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    snapshot = {
        "id": snapshot_id, "published_at": published_at,
        "captured_at": now.isoformat(), "publication_date": published.astimezone(IST).date().isoformat(),
        "cost_bps": cost_bps, "model": model,
        "model_fingerprint": hashlib.sha256(json.dumps(model, sort_keys=True).encode()).hexdigest(),
        "universe": sorted({r["symbol"] for r in rows}), "picks": {},
    }
    for horizon in HORIZONS:
        rank = f"rank_{horizon}d"
        candidates = [r for r in rows if r.get(rank) is not None and 0 < r[rank] <= 20]
        candidates.sort(key=lambda r: (r[rank], r["symbol"]))
        snapshot["picks"][str(horizon)] = [
            {"symbol": r["symbol"], "rank": r[rank], "score": r.get(f"score_{horizon}d")}
            for r in candidates
        ]
    # Publish a fully written file exclusively: retries cannot replace it, and a
    # crash during serialization cannot leave a truncated prediction record.
    fd, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(snapshot, fh, allow_nan=False, indent=2)
        try:
            os.link(temporary, path)
        except FileExistsError:
            pass
    finally:
        os.unlink(temporary)
    return snapshot_id


def evaluate(snapshot, horizon, prices, sessions, cost_bps=None, corporate_events=None):
    """Prices are adjusted daily closes keyed by (symbol, ISO date).

    Entry is strictly after publication's IST date; all exits use the SAME index
    calendar date, rather than shifting by each stock's available row count.
    """
    picks = snapshot["picks"][str(horizon)]
    events = load_events() if corporate_events is None else corporate_events
    quality_events = set()
    result = {
        "id": f"{snapshot['id']}-{horizon}", "snapshot_id": snapshot["id"],
        "published_at": snapshot["published_at"], "horizon": horizon,
        "model_fingerprint": snapshot["model_fingerprint"], "model": snapshot["model"],
        "pick_count": len(picks), "status": "pending", "entry_date": None,
        "exit_date": None, "elapsed_sessions": 0,
        "picks": [{**p, "gross_return": None, "net_return": None, "beat_universe": None} for p in picks],
        "net_return": None, "gross_return": None, "universe_return": None,
        "index_return": None, "excess_return": None, "hit_rate": None,
        "worst_pick_return": None, "universe_coverage": 0,
        "reason": "Waiting for the first completed session after publication",
    }
    future = [d for d in sessions if d > snapshot["publication_date"]]
    if not future:
        return result
    entry = future[0]
    result.update(entry_date=entry, elapsed_sessions=min(len(future) - 1, horizon))
    if len(future) <= horizon:
        result["reason"] = "Holding window has not finished"
        return result
    exit_date = future[horizon]
    result["exit_date"] = exit_date

    def realized(symbol):
        value, reasons = adjusted_return(symbol, entry, exit_date, prices, events)
        quality_events.update(r for r in reasons if r != "missing_price")
        return value

    universe = [realized(symbol) for symbol in snapshot["universe"]]
    available = [r for r in universe if r is not None]
    result["universe_coverage"] = len(available)
    benchmark = sum(available) / len(available) if len(available) == len(universe) else None
    cost = (snapshot["cost_bps"] if cost_bps is None else cost_bps) / 10000
    # Each independent equal-weight cohort pays an entry AND exit cost.
    def net(gross):
        return (1 + gross) * (1 - cost) / (1 + cost) - 1

    result["picks"] = []
    for pick in picks:
        gross = realized(pick["symbol"])
        result["picks"].append({**pick, "gross_return": gross,
                                "net_return": net(gross) if gross is not None else None,
                                "entry_close": prices.get((pick["symbol"], entry)),
                                "exit_close": prices.get((pick["symbol"], exit_date)),
                                "beat_universe": gross > benchmark + 1e-12 if gross is not None and benchmark is not None else None})
    result["universe_return"] = benchmark
    result["index_return"] = realized("^NSEI")
    result["quality_events"] = sorted(quality_events)
    returns = [p["gross_return"] for p in result["picks"] if p["gross_return"] is not None]
    if len(returns) != len(picks) or not picks or benchmark is None or result["index_return"] is None:
        reason = ("Unresolved corporate-action entitlements: " + ", ".join(sorted(quality_events))
                  if quality_events else "Missing entry/exit prices; no stocks are dropped from the frozen cohort or universe")
        result.update(status="incomplete", reason=reason)
        return result
    gross = sum(returns) / len(returns)
    result.update(status="complete", reason=None, gross_return=gross,
                  net_return=net(gross), excess_return=net(gross) - benchmark,
                  hit_rate=sum(r > benchmark + 1e-12 for r in returns) / len(returns),
                  worst_pick_return=min(returns))
    return result


def summaries(cohorts):
    result = {}
    for horizon in HORIZONS:
        all_rows = [r for r in cohorts if r["horizon"] == horizon]
        complete = sorted([r for r in all_rows if r["status"] == "complete"], key=lambda r: (r["entry_date"], r["published_at"]))
        nonoverlap, last_exit = [], ""
        for row in complete:
            if row["entry_date"] >= last_exit:
                nonoverlap.append(row)
                last_exit = row["exit_date"]
        def mean(rows, key):
            return sum(r[key] for r in rows) / len(rows) if rows else None
        months = defaultdict(list)
        for row in complete:
            months[row["entry_date"][:7]].append(row)
        result[str(horizon)] = {
            "total": len(all_rows), "complete": len(complete),
            "pending": sum(r["status"] == "pending" for r in all_rows),
            "incomplete": sum(r["status"] == "incomplete" for r in all_rows),
            "mean_net_return": mean(complete, "net_return"),
            "mean_universe_return": mean(complete, "universe_return"),
            "mean_index_return": mean(complete, "index_return"),
            "mean_hit_rate": mean(complete, "hit_rate"),
            "cohort_win_rate": mean([{"win": float(r["excess_return"] > 0)} for r in complete], "win"),
            "worst_net_return": min((r["net_return"] for r in complete), default=None),
            "worst_pick_return": min((r["worst_pick_return"] for r in complete), default=None),
            "nonoverlapping_count": len(nonoverlap),
            "nonoverlapping_mean_excess": mean(nonoverlap, "excess_return"),
            "months": [{"month": month, "cohorts": len(rows), "net_return": mean(rows, "net_return"),
                        "universe_return": mean(rows, "universe_return"), "index_return": mean(rows, "index_return"),
                        "win_rate": sum(r["excess_return"] > 0 for r in rows) / len(rows)}
                       for month, rows in sorted(months.items())],
        }
    return result


def build_report(root, prices, sessions, today):
    root = Path(root)
    # Today's bar can still be partial. Exclude it even after market close.
    sessions = sorted({d for d in sessions if d < today})
    report_path = root / "report.json"
    previous = json.loads(report_path.read_text()) if report_path.exists() else {"cohorts": []}
    events = load_events()
    method_version = "corporate-actions-v2-" + fingerprint(events)
    compatible = previous.get("method_version") == method_version
    if report_path.exists() and not compatible:
        archive = root / "report_history" / (hashlib.sha256(report_path.read_bytes()).hexdigest() + ".json")
        atomic_json(archive, previous)
    settled = {r["id"]: r for r in previous["cohorts"] if r["status"] == "complete"} if compatible else {}
    cohorts = []
    for path in sorted((root / "predictions").glob("*.json")):
        snapshot = json.loads(path.read_text())
        for horizon in HORIZONS:
            key = f"{snapshot['id']}-{horizon}"
            cohorts.append(settled[key] if key in settled else evaluate(snapshot, horizon, prices, sessions, corporate_events=events))
    report = {"updated_at": utc_now(), "method": "next-session-close", "method_version": method_version,
              "quality_policy": "Unresolved complex actions remain incomplete; no price-only losses are substituted for share entitlements.", "summaries": summaries(cohorts),
              "cohorts": sorted(cohorts, key=lambda r: r["published_at"], reverse=True)}
    atomic_json(report_path, report)
    return report


def read_report(root=DEFAULT_DIR):
    path = Path(root) / "report.json"
    if path.exists():
        return json.loads(path.read_text())
    return {"updated_at": None, "method": "next-session-close", "summaries": summaries([]), "cohorts": []}
