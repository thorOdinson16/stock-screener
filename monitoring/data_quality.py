"""
data_quality.py — lightweight data-quality checks beyond SeaTunnel dead-lettering.

Checks the latest screener snapshot for:
  * duplicate symbols;
  * stale quotes (a symbol's trade_date far behind the snapshot's);
  * fundamentals outliers using a robust (median/MAD) z-score.

    python monitoring/data_quality.py            # checks latest Druid snapshot
    python monitoring/data_quality.py --out DIR

Pure functions are unit-tested (tests/test_data_quality.py).
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from collect_metrics import druid_sql, pipeline_env  # noqa: E402

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_DEFAULT_OUT = os.path.join(_REPO_ROOT, "monitoring", "quality")

FUNDAMENTAL_COLUMNS = ["pe_ratio", "pb_ratio", "dividend_yield",
                       "return_on_equity", "debt_to_equity"]
MAD_SCALE = 0.6745


def find_duplicates(df: pd.DataFrame, key: str = "symbol"):
    dupes = df[df.duplicated(key, keep=False)]
    return sorted(dupes[key].dropna().unique().tolist())


def find_stale(df: pd.DataFrame, max_age_days: int = 5,
               date_col: str = "trade_date"):
    """Symbols whose trade_date is more than `max_age_days` behind the latest
    trade_date in the snapshot."""
    if df.empty or date_col not in df.columns:
        return []
    dates = pd.to_datetime(df[date_col], errors="coerce")
    if dates.isna().all():
        return []
    as_of = dates.max()
    age = (as_of - dates).dt.days
    return sorted(df.loc[age > max_age_days, "symbol"].dropna().unique().tolist())


def robust_outliers(df: pd.DataFrame, columns=None, z_threshold: float = 8.0):
    """Flags values whose robust z-score |0.6745*(x-median)/MAD| exceeds the
    threshold, per column. Returns {column: [{symbol, value, z}, ...]}."""
    columns = columns or FUNDAMENTAL_COLUMNS
    result = {}
    for column in columns:
        if column not in df.columns:
            continue
        values = pd.to_numeric(df[column], errors="coerce")
        valid = values.dropna()
        if len(valid) < 4:
            continue
        median = valid.median()
        mad = (valid - median).abs().median()
        if mad == 0:
            continue
        z = MAD_SCALE * (values - median) / mad
        flagged = df.loc[z.abs() > z_threshold, ["symbol", column]].copy()
        if flagged.empty:
            continue
        flagged["z"] = z[flagged.index].round(3)
        flagged = flagged.rename(columns={column: "value"})
        result[column] = flagged.to_dict(orient="records")
    return result


def latest_snapshot(env) -> pd.DataFrame:
    url = env.get("DRUID_URL", "http://localhost:8888")
    rows = druid_sql(
        "SELECT * FROM screener WHERE __time = "
        "(SELECT MAX(__time) FROM screener)",
        url,
    )
    return pd.DataFrame(rows)


def main():
    parser = argparse.ArgumentParser(description="Data-quality checks")
    parser.add_argument("--out", default=_DEFAULT_OUT)
    parser.add_argument("--max-age-days", type=int, default=5)
    args = parser.parse_args()

    env = pipeline_env()
    try:
        snapshot = latest_snapshot(env)
    except Exception as e:  # noqa: BLE001
        raise SystemExit(f"Could not load the latest screener snapshot: {e}")

    report = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "rows": int(len(snapshot)),
        "duplicates": find_duplicates(snapshot),
        "stale": find_stale(snapshot, max_age_days=args.max_age_days),
        "fundamental_outliers": robust_outliers(snapshot),
    }

    os.makedirs(args.out, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = os.path.join(args.out, f"{stamp}.json")
    with open(path, "w") as fh:
        json.dump(report, fh, indent=2, default=str)
    print(f"wrote {path}: {len(report['duplicates'])} duplicates, "
          f"{len(report['stale'])} stale, "
          f"{sum(len(v) for v in report['fundamental_outliers'].values())} outliers")


if __name__ == "__main__":
    main()
