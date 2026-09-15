"""
screening_common.py — shared helpers for the screening DAGs.

Not a DAG itself; imported by the DAG files in this folder.
"""

from pathlib import Path

# airflow/dags/screening_common.py -> repo root
REPO_ROOT = str(Path(__file__).resolve().parents[2])

# Shared Airflow pool (1 slot) so on-demand runs and retrains never overlap.
POOL = "screening"

# Jinja template that exports dag_run.conf options to the step scripts.
RUN_EXPORTS = (
    "export FULL_RUN=\"{{ 1 if (dag_run.conf or {}).get('full', false) else 0 }}\"; "
    "export UNIVERSE_LIMIT=\"{{ (dag_run.conf or {}).get('universe_limit') or '' }}\"; "
    "export PUBLISH_HISTORY=\"{{ 1 if (dag_run.conf or {}).get('publish_history', false) else 0 }}\"; "
)


def step(script: str, args: str = "") -> str:
    """Build a BashOperator command that runs a pipeline step script.

    Note: the command must NOT end in '.sh' — Airflow treats a templated string
    with a known template extension as a template *file path*. The trailing
    comment keeps it a plain command.
    """
    command = " ".join(part for part in ("bash", f"{REPO_ROOT}/scripts/{script}", args) if part)
    return f"{RUN_EXPORTS}{command} # screening step"
