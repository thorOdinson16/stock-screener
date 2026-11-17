"""
universe.py — loads the cached NIFTY 500 constituent metadata (company name and
industry) used to enrich Druid rows with human-readable names/sectors.
"""

import json
import os
from functools import lru_cache

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_CACHE_PATH = os.path.join(_REPO_ROOT, "poller", "universe", "universe_cache.json")


@lru_cache(maxsize=2)
def _load_file(_mtime_ns: int) -> dict[str, dict]:
    try:
        with open(_CACHE_PATH) as fh:
            stocks = json.load(fh)["stocks"]
    except (OSError, KeyError, json.JSONDecodeError):
        return {}
    return {s["yf_symbol"]: {"company_name": s["company_name"], "industry": s["industry"]} for s in stocks}


def _load() -> dict[str, dict]:
    """The poller refreshes this file weekly; keying the cache on its mtime lets a
    long-running API pick the new constituents up without a restart."""
    try:
        mtime = os.stat(_CACHE_PATH).st_mtime_ns
    except OSError:
        mtime = 0
    return _load_file(mtime)


def metadata_for(symbol: str) -> dict:
    return _load().get(symbol, {"company_name": symbol, "industry": "Unknown"})


def industries() -> list[str]:
    return sorted({m["industry"] for m in _load().values()})


def size() -> int:
    return len(_load())
