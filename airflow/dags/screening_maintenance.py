"""
screening_maintenance.py — Iceberg table maintenance (compaction, manifest
rewrite, snapshot expiry).

Triggered from the dashboard's Maintenance action. schedule=None (on-demand).
"""

from datetime import datetime, timedelta

from airflow import DAG
from airflow.providers.standard.operators.bash import BashOperator

from screening_common import POOL, step

with DAG(
    dag_id="screening_maintenance",
    description="Iceberg compaction, manifest rewrite and snapshot expiry",
    start_date=datetime(2026, 9, 1),
    schedule=None,
    catchup=False,
    max_active_runs=1,
    tags=["screening", "maintenance"],
    default_args={"owner": "screening", "retries": 0, "execution_timeout": timedelta(hours=1)},
) as dag:
    maintenance = BashOperator(
        task_id="maintenance", bash_command=step("maintenance.sh"), pool=POOL
    )
