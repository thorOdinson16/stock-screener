"""Read-only forecast diagnostics and strictly gated live forecast cards."""

from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import re
from fastapi import APIRouter, HTTPException, Query

from api.druid import query, quote, LATEST_SNAPSHOT, DruidError
from ml.evaluation.calibration import read_artifact, forecast
from ml.evaluation.corporate_actions import load_events, fingerprint
from ml.evaluation.entitlements import audit as entitlement_audit
from ml.evaluation.forecast_outcomes import feature_event_digest
from ml.evaluation.retail import Policy, eligibility, number

ROOT = Path(__file__).resolve().parents[1]
router = APIRouter(prefix="/api/forecasts", tags=["forecast evidence"])


def lineage_matches(artifact, horizon):
    selected_path, registry_path = ROOT / "ml/models/selected.json", ROOT / "ml/models/registry.json"
    if not selected_path.exists() or not registry_path.exists():
        return False
    selected, registry = json.loads(selected_path.read_text()), json.loads(registry_path.read_text())
    events = load_events()
    if selected.get("quality_policy_digest") != fingerprint(events) or registry.get("quality_policy_digest") != fingerprint(events):
        return False
    if artifact.get("events_digest") != fingerprint(events):
        return False
    lineage = artifact.get("lineage", {})
    if lineage.get("feature_events_digest") != feature_event_digest(events):
        return False
    meta = selected.get("labels", {}).get(f"excess_ret_{horizon}d", {})
    if meta.get("model_name") != "rank_ensemble":
        return False
    if sorted(m.get("model_name") for m in meta.get("members", [])) != ["gbt_rank", "rf_rank"]:
        return False
    registered = registry.get("labels", {}).get(f"excess_ret_{horizon}d", {})
    for member in meta["members"]:
        name = member["model_name"]
        if member.get("path") != registered.get(name, {}).get("path"):
            return False
        if registered[name].get("params") != lineage.get("models", {}).get(name):
            return False
    return registry.get("features") == lineage.get("variants", {}).get(f"rank{horizon}", {}).get("features")


def card_for_row(artifact, row, horizon, universe_size, policy=None):
    rank = row.get(f"rank_{horizon}d") or row.get("rank")
    reference = row.get("close")
    asof = datetime.now(timezone(timedelta(hours=5, minutes=30))).date()
    try:
        age = (asof - datetime.fromisoformat(str(row["trade_date"])[:10]).date()).days
        fresh = 0 <= age <= 5
        context = {"rank": int(rank), "rank_percentile": 1 - (rank - 1) / universe_size,
                   "volatility": float(row["volatility_20d"])}
    except (KeyError, TypeError, ValueError, ZeroDivisionError, OverflowError):
        fresh, context = False, None
    policy_matches = True
    try:
        audited_policy = Policy(**{**artifact.get("policy", {}), "horizon": horizon})
        volume, ratio = number(row.get("volume")), number(row.get("volume_ratio"))
        average = row.get("volume_avg_20d") or (volume / ratio if volume and ratio and ratio > 0 else None)
        eligible = not eligibility({**row, "rank": rank, "volume_avg_20d": average}, policy or audited_policy, asof.isoformat(), load_events())
    except (TypeError, ValueError):
        eligible = False
    if policy is not None:
        calibrated = artifact.get("policy", {})
        for field in ("stop_pct", "fee_bps", "slippage_bps", "flat_fee", "min_turnover", "max_volatility", "max_age_days", "top_rank"):
            if getattr(policy, field) != calibrated.get(field):
                policy_matches = False
    result = forecast(artifact, horizon, context, reference,
                    {"fresh": fresh, "lineage_matches": lineage_matches(artifact, horizon), "policy_matches": policy_matches})
    if not eligible:
        return {**result, "status": "insufficient_evidence", "profit_probability": None,
                "outperform_probability": None, "net_return_band": None, "exit_value_band": None,
                "reasons": result["reasons"] + ["Stock is outside the audited eligibility sample"]}
    return result


@router.get("/report")
def report():
    artifact = read_artifact()
    artifact["entitlements"] = entitlement_audit()
    artifact["evidence_current"] = artifact.get("events_digest") == fingerprint(load_events())
    if not artifact["evidence_current"] and artifact.get("status") != "not_available":
        artifact["status"] = "stale_evidence"
    return artifact


@router.get("/{symbol}")
def stock_forecast(symbol: str, horizon: int = Query(21, ge=1, le=252)):
    symbol = symbol.strip().upper()
    if not re.fullmatch(r"[A-Z0-9&^._-]{1,40}", symbol):
        raise HTTPException(status_code=422, detail="Invalid symbol")
    if "." not in symbol and not symbol.startswith("^"):
        symbol += ".NS"
    artifact = read_artifact()
    evidence = artifact.get("horizons", {}).get(str(horizon), {})
    # Unsupported evidence never triggers market queries just to manufacture a
    # reference-price forecast. The UI still receives useful failure reasons.
    if not evidence.get("production_ready") or artifact.get("source_status") != "verified":
        return {"symbol": symbol, **forecast(artifact, horizon)}
    try:
        rows = query(f"SELECT * FROM screener WHERE __time = {LATEST_SNAPSHOT}")
    except DruidError:
        return {"symbol": symbol, **forecast(artifact, horizon)}
    row = next((r for r in rows if r["symbol"] == symbol), None)
    if row is None:
        return {"symbol": symbol, **forecast(artifact, horizon)}
    size = sum(r.get(f"rank_{horizon}d") is not None and r.get(f"rank_{horizon}d", 0) > 0 for r in rows)
    return {"symbol": symbol, **card_for_row(artifact, row, horizon, size)}
