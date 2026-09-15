"""
screening_on_demand.py — one-shot full screening pipeline.

Triggered from the dashboard's "Run pipeline" button (or manually in the Airflow
UI). Options come via dag_run.conf:

    full            bool  — also poll fundamentals (default false = quotes only)
    universe_limit  int   — cap the polled universe (default: full NIFTY 500)
    publish_history bool  — also publish daily price history to Druid
"""

from datetime import datetime, timedelta

from airflow import DAG
from airflow.providers.standard.operators.bash import BashOperator

from screening_common import POOL, step

with DAG(
    dag_id="screening_on_demand",
    description="On-demand NIFTY 500 screen: poll -> ingest -> indicators -> score -> serve -> Druid",
    start_date=datetime(2026, 9, 1),
    schedule=None,
    catchup=False,
    max_active_runs=1,
    tags=["screening", "on-demand"],
    default_args={"owner": "screening", "retries": 0, "execution_timeout": timedelta(minutes=45)},
) as dag:
    preflight = BashOperator(task_id="preflight", bash_command=step("preflight.sh"), pool=POOL)
    poll = BashOperator(task_id="poll", bash_command=step("poll.sh"), pool=POOL)
    ingest = BashOperator(task_id="ingest", bash_command=step("ingest.sh"), pool=POOL)
    indicators = BashOperator(task_id="indicators", bash_command=step("indicators.sh"), pool=POOL)
    score = BashOperator(task_id="score", bash_command=step("score.sh"), pool=POOL)
    serve = BashOperator(task_id="serve", bash_command=step("serving.sh"), pool=POOL)
    wait_druid = BashOperator(
        task_id="wait_for_druid",
        bash_command=step("wait_druid.sh", "240"),
        pool=POOL,
        execution_timeout=timedelta(minutes=10),
    )

    preflight >> poll >> ingest >> indicators >> score >> serve >> wait_druid
