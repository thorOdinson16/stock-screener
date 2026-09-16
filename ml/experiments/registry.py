"""
registry.py — lightweight JSON experiment registry (no MLflow).

Each run is written to ml/experiments/<timestamp>_<name>.json with the git
commit, config, and metrics, and appended to ml/experiments/index.json so runs
are comparable and reproducible.
"""

import json
import os
import subprocess
from datetime import datetime, timezone


def git_commit(repo_root: str):
    try:
        return subprocess.check_output(
            ["git", "-C", repo_root, "rev-parse", "HEAD"], stderr=subprocess.DEVNULL
        ).decode().strip()
    except Exception:  # noqa: BLE001 — registry must never fail the run
        return None


def record(name: str, payload: dict, repo_root: str, out_dir: str) -> str:
    now = datetime.now(timezone.utc)
    entry = {
        "name": name,
        "recorded_at": now.isoformat(),
        "git_commit": git_commit(repo_root),
        **payload,
    }
    os.makedirs(out_dir, exist_ok=True)
    filename = f"{now.strftime('%Y%m%dT%H%M%SZ')}_{name}.json"
    path = os.path.join(out_dir, filename)
    with open(path, "w") as fh:
        json.dump(entry, fh, indent=2, default=str)

    index_path = os.path.join(out_dir, "index.json")
    index = []
    if os.path.exists(index_path):
        try:
            with open(index_path) as fh:
                index = json.load(fh)
        except (json.JSONDecodeError, OSError):
            index = []
    index.append(
        {
            "name": name,
            "file": filename,
            "recorded_at": entry["recorded_at"],
            "git_commit": entry["git_commit"],
        }
    )
    with open(index_path, "w") as fh:
        json.dump(index, fh, indent=2)
    return path
