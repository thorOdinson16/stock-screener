"""
screening_retrain.py — rebuild the training set, retrain and re-evaluate the
scoring models, then refresh ml/models/selected.json.

Triggered from the dashboard's "Retrain model" action.
"""

from datetime import datetime, timedelta

from airflow import DAG
from airflow.providers.standard.operators.bash import BashOperator

from screening_common import POOL, step

with DAG(
    dag_id="screening_retrain",
    description="Rebuild training data, retrain models, re-evaluate and select",
    start_date=datetime(2026, 9, 1),
    schedule=None,
    catchup=False,
    max_active_runs=1,
    tags=["screening", "retrain"],
    default_args={"owner": "screening", "retries": 0, "execution_timeout": timedelta(hours=2)},
) as dag:
    retrain = BashOperator(task_id="retrain", bash_command=step("retrain.sh"), pool=POOL)
