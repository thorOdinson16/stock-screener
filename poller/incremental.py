"""Pure helpers for resuming daily downloads from committed Iceberg bars."""

from datetime import date, timedelta
import math


def history_start(symbol, watermarks, overlap_days=5):
    if overlap_days < 0:
        raise ValueError("overlap_days must be non-negative")
    latest = watermarks.get(symbol)
    if latest is None:
        return None  # New symbols use the configured bootstrap period.
    return (date.fromisoformat(latest) - timedelta(days=overlap_days)).isoformat()


def adjustment_revision(records, reference, tolerance=.005):
    """Detect a provider's restatement of historical adjusted prices.

    A single partial/latest bar changing is normal. Broad overlap scaling means
    older stored bars must be fetched again on the same adjustment basis.
    """
    ratios = []
    for row in records:
        old = reference.get("closes", {}).get(row["trade_date"])
        new = row.get("close")
        if old and new and old > 0 and new > 0 and math.isfinite(new):
            ratios.append(new / old)
    changed = [r for r in ratios if abs(r - 1) > tolerance]
    if records and reference.get("closes") and not ratios:
        return True
    if len(ratios) == 1 and abs(ratios[0] - 1) > .25:
        return True
    return len(changed) >= 2 and len(changed) / len(ratios) >= .6
