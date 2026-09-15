"""
pipeline.py — API endpoints that trigger and supervise the Airflow screening
DAGs on behalf of the dashboard.

    POST /api/pipeline/run       trigger screening_on_demand
    POST /api/pipeline/retrain   trigger screening_retrain
    GET  /api/pipeline/status    latest run + per-step states
    GET  /api/pipeline/runs      recent runs
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api import airflow as af

router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])


class RunRequest(BaseModel):
    full: bool = False
    universe_limit: int | None = None
    publish_history: bool = False


def _steps_for(dag_id: str, task_instances: list[dict]) -> list[dict]:
    order = af.ON_DEMAND_STEPS if dag_id == af.DAG_ON_DEMAND else af.RETRAIN_STEPS
    by_id = {t.get("task_id"): t for t in task_instances}
    return [
        {"id": step, "state": (by_id.get(step) or {}).get("state") or "none"}
        for step in order
    ]


def _serialize(dag_id: str, run: dict, task_instances: list[dict] | None = None) -> dict:
    conf = run.get("conf") or {}
    if dag_id == af.DAG_RETRAIN:
        run_type = "retrain"
    else:
        run_type = "full" if conf.get("full") else "quick"
    return {
        "dag_id": dag_id,
        "dag_run_id": run.get("dag_run_id"),
        "state": run.get("state"),
        "run_type": run_type,
        "conf": conf,
        "start_date": run.get("start_date") or run.get("logical_date"),
        "end_date": run.get("end_date"),
        "active": run.get("state") in af.ACTIVE_STATES,
        "steps": _steps_for(dag_id, task_instances or []),
        "airflow_url": f"{af.ui_base_url()}/dags/{dag_id}/grid",
    }


@router.post("/run")
def run_pipeline(req: RunRequest):
    active = af.find_active_run()
    if active:
        raise HTTPException(
            status_code=409,
            detail={"message": "A pipeline run is already active", "run": _serialize(active["dag_id"], active)},
        )
    try:
        run = af.trigger_dag(
            af.DAG_ON_DEMAND,
            {"full": req.full, "universe_limit": req.universe_limit, "publish_history": req.publish_history},
        )
    except af.AirflowError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    return _serialize(af.DAG_ON_DEMAND, run)


@router.post("/retrain")
def retrain_pipeline():
    active = af.find_active_run()
    if active:
        raise HTTPException(
            status_code=409,
            detail={"message": "A pipeline run is already active", "run": _serialize(active["dag_id"], active)},
        )
    try:
        run = af.trigger_dag(af.DAG_RETRAIN, {})
    except af.AirflowError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    return _serialize(af.DAG_RETRAIN, run)


@router.get("/status")
def pipeline_status(dag_id: str | None = None, run_id: str | None = None):
    try:
        if dag_id and run_id:
            run = af.get_dag_run(dag_id, run_id)
            tis = af.list_task_instances(dag_id, run_id)
            return _serialize(dag_id, run, tis)

        latest = None
        for candidate in (af.DAG_ON_DEMAND, af.DAG_RETRAIN):
            for run in af.list_dag_runs(candidate, limit=5):
                key = run.get("start_date") or run.get("logical_date") or ""
                if latest is None or key > latest[0]:
                    latest = (key, candidate, run)
    except af.AirflowError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

    if latest is None:
        return {"state": "none", "active": False, "steps": []}

    _, candidate, run = latest
    tis = af.list_task_instances(candidate, run["dag_run_id"])
    return _serialize(candidate, run, tis)


@router.get("/runs")
def pipeline_runs(limit: int = 10):
    runs = []
    try:
        for candidate in (af.DAG_ON_DEMAND, af.DAG_RETRAIN):
            for run in af.list_dag_runs(candidate, limit=limit):
                runs.append(_serialize(candidate, run))
    except af.AirflowError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

    runs.sort(key=lambda r: r.get("start_date") or "", reverse=True)
    return {"runs": runs[:limit]}
