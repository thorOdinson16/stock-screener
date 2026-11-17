"""
Shared pytest setup.

* The repository root goes on sys.path so `ml.evaluation...` / `api...` import
  as packages.
* `poller/` goes on sys.path so its scripts import flat (`import backfill`),
  exactly as they run in production. Tests must NOT use `import poller.x`:
  with poller/ on the path, `poller` resolves to the module poller/poller.py
  rather than the directory.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "poller", ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
