"""
airflow.py — minimal Airflow 3 REST API client for triggering/supervising the
screening DAGs.

Credentials come from config/airflow.env (gitignored), overridable by
AIRFLOW_* environment variables. A JWT is obtained from the simple-auth token
endpoint and cached in-process.
"""

import logging
import os
import threading
import time
from datetime import datetime, timezone

import requests

logger = logging.getLogger(__name__)

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_CONFIG_PATH = os.path.join(_REPO_ROOT, "config", "airflow.env")

_KEYS = ("AIRFLOW_API_URL", "AIRFLOW_AUTH_URL", "AIRFLOW_USER", "AIRFLOW_PASSWORD")

DAG_ON_DEMAND = "screening_on_demand"
DAG_RETRAIN = "screening_retrain"

# UI step order for the on-demand DAG.
ON_DEMAND_STEPS = ["preflight", "poll", "ingest", "indicators", "score", "serve", "wait_for_druid"]
RETRAIN_STEPS = ["retrain"]

ACTIVE_STATES = {"queued", "running"}


class AirflowError(RuntimeError):
    pass


def _load_config() -> dict:
    cfg: dict[str, str] = {}
    if os.path.exists(_CONFIG_PATH):
        with open(_CONFIG_PATH) as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                cfg[key.strip()] = value.strip()
    for key in _KEYS:
        if os.environ.get(key):
            cfg[key] = os.environ[key]
    for key in _KEYS:
        if not cfg.get(key):
            raise AirflowError(f"Missing Airflow config '{key}' (config/airflow.env)")
    return cfg


def ui_base_url() -> str:
    return _load_config()["AIRFLOW_API_URL"].rstrip("/").removesuffix("/api/v2")


_token = {"value": None, "exp": 0.0}
_token_lock = threading.Lock()


def _token_value(force: bool = False) -> str:
    cfg = _load_config()
    with _token_lock:
        now = time.time()
        if not force and _token["value"] and now < _token["exp"]:
            return _token["value"]
        resp = requests.post(
            cfg["AIRFLOW_AUTH_URL"],
            json={"username": cfg["AIRFLOW_USER"], "password": cfg["AIRFLOW_PASSWORD"]},
            timeout=15,
        )
        if not resp.ok:
            raise AirflowError(f"Airflow auth failed ({resp.status_code}): {resp.text[:200]}")
        token = resp.json()["access_token"]
        _token.update(value=token, exp=now + 1800)
        return token


def _request(method: str, path: str, **kwargs):
    cfg = _load_config()
    url = cfg["AIRFLOW_API_URL"].rstrip("/") + path
    for attempt in range(2):
        headers = {"Authorization": f"Bearer {_token_value(force=attempt == 1)}"}
        resp = requests.request(method, url, headers=headers, timeout=30, **kwargs)
        if resp.status_code == 401 and attempt == 0:
            continue
        if not resp.ok:
            raise AirflowError(f"Airflow {method} {path} failed ({resp.status_code}): {resp.text[:300]}")
        return resp.json() if resp.content else {}
    raise AirflowError(f"Airflow {method} {path} unauthorized")


def trigger_dag(dag_id: str, conf: dict | None = None) -> dict:
    now = datetime.now(timezone.utc)
    dag_run_id = f"manual__{now.strftime('%Y-%m-%dT%H:%M:%S.%f')}"
    return _request(
        "POST",
        f"/dags/{dag_id}/dagRuns",
        json={"dag_run_id": dag_run_id, "logical_date": now.isoformat(), "conf": conf or {}},
    )


def get_dag_run(dag_id: str, run_id: str) -> dict:
    return _request("GET", f"/dags/{dag_id}/dagRuns/{run_id}")


def list_dag_runs(dag_id: str, limit: int = 10) -> list[dict]:
    data = _request("GET", f"/dags/{dag_id}/dagRuns?limit={limit}")
    return data.get("dag_runs", [])


def list_task_instances(dag_id: str, run_id: str) -> list[dict]:
    data = _request("GET", f"/dags/{dag_id}/dagRuns/{run_id}/taskInstances")
    return data.get("task_instances", [])


def find_active_run() -> dict | None:
    """Returns the first active run across the screening DAGs, if any."""
    for dag_id in (DAG_ON_DEMAND, DAG_RETRAIN):
        try:
            for run in list_dag_runs(dag_id, limit=5):
                if run.get("state") in ACTIVE_STATES:
                    return {"dag_id": dag_id, **run}
        except AirflowError:
            continue
    return None
