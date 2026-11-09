"""Paper trade planning from the latest served snapshot; no broker execution."""

from datetime import datetime, timezone, timedelta
from typing import Any
import json
from pathlib import Path
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ml.evaluation.corporate_actions import load_events, fingerprint
from ml.evaluation.retail import Policy, plan, number
from api.druid import query, LATEST_SNAPSHOT, DruidError

router = APIRouter(prefix="/api", tags=["retail research"])


@router.get("/research/baselines")
def baselines():
    from ml.evaluation.baseline_report import read_report
    return read_report()


@router.get("/research/tournament")
def tournament():
    from ml.evaluation.tournament_report import read_report
    return read_report()


@router.get("/research/completion")
def completion():
    from ml.evaluation.completion_report import read_report
    return read_report()


@router.get("/research/equations")
def equations():
    from ml.evaluation.quant_report import read_report
    return read_report()


class Holding(BaseModel):
    symbol: str = Field(min_length=1, max_length=40)
    quantity: int = Field(gt=0)
    price: float = Field(gt=0, allow_inf_nan=False)
    industry: str = "Unknown"


class PlanRequest(BaseModel):
    settings: dict[str, float | int] = Field(default_factory=dict)
    holdings: list[Holding] = Field(default_factory=list, max_length=100)


@router.get("/research/quality")
def quality():
    events = load_events()
    selected_path = Path(__file__).resolve().parents[1] / "ml/models/selected.json"
    selected = json.loads(selected_path.read_text()) if selected_path.exists() else {}
    return {"status": "research", "corporate_actions": events,
            "complex_actions_resolved": False,
            "model_uses_current_quality_policy": selected.get("quality_policy_digest") == fingerprint(events),
            "probabilities_calibrated": False,
            "notes": ["Complex-action valuations need sourced entitlement data.",
                      "Current models produce relative rankings, not calibrated probabilities or guaranteed target prices."]}


@router.get("/research/experiments")
def experiments():
    results = Path(__file__).resolve().parents[1] / "ml/evaluation/results"
    reports = list(results.glob("retail_research*/report.json"))
    if not reports:
        return {"status": "not_available", "results": {}, "production_changed": False,
                "selected_on_validation": None, "limitations": []}
    latest = max(reports, key=lambda p: p.stat().st_mtime)
    return json.loads(latest.read_text())


@router.post("/trade-plan")
def trade_plan(request: PlanRequest):
    try:
        settings = dict(request.settings)
        for key in ("horizon", "max_positions", "max_age_days", "top_rank"):
            if key in settings and int(settings[key]) == settings[key]:
                settings[key] = int(settings[key])
        policy = Policy(**settings).validate()
        if policy.horizon not in (5, 21):
            raise ValueError("Served rankings currently support 5 or 21 sessions")
    except (TypeError, ValueError, OverflowError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    try:
        rows = query(f"SELECT * FROM screener WHERE __time = {LATEST_SNAPSHOT}")
    except DruidError as error:
        raise HTTPException(status_code=503, detail="Market snapshot is unavailable. Start the stack and run the pipeline.") from error
    asof = datetime.now(timezone(timedelta(hours=5, minutes=30))).date().isoformat()
    for row in rows:
        row["rank"] = row.get(f"rank_{policy.horizon}d")
        row["score"] = row.get(f"score_{policy.horizon}d")
        volume, ratio = number(row.get("volume")), number(row.get("volume_ratio"))
        row["volume_avg_20d"] = volume / ratio if volume and ratio and ratio > 0 else None
    holdings = [h.model_dump() for h in request.holdings]
    for holding in holdings:
        holding["symbol"] = holding["symbol"].strip().upper()
        if "." not in holding["symbol"] and not holding["symbol"].startswith("^"):
            holding["symbol"] += ".NS"
        served = next((r for r in rows if r["symbol"] == holding["symbol"]), None)
        if served and served.get("industry"):
            holding["industry"] = served["industry"]
    try:
        result = plan(rows, policy, asof, holdings)
        from api.forecasts import card_for_row
        from ml.evaluation.calibration import read_artifact
        artifact = read_artifact()
        size = sum(r.get("rank") is not None and r.get("rank", 0) > 0 for r in rows)
        by_symbol = {r["symbol"]: r for r in rows}
        for candidate in result["candidates"]:
            candidate["forecast"] = card_for_row(artifact, by_symbol[candidate["symbol"]], policy.horizon, size, policy)
        return result
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("/research/information")
def information_research():
    from ml.evaluation.information_report import read_report
    return read_report()


@router.get("/research/financial-pilot")
def financial_pilot():
    from ml.evaluation.pilot_report import read_report
    return read_report()
