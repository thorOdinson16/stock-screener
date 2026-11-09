"""Auditable demerger wealth from verified terms and explicit raw quotes.

Never backfill an unlisted entitlement using a later listing price. Never infer
security identity from a ticker that was reused during a restructuring.
"""

import argparse
import csv
from datetime import date
import json
import math
from pathlib import Path

try:
    from .corporate_actions import load_events
except ImportError:
    from corporate_actions import load_events


def read_quotes(path):
    quotes = {}
    with Path(path).open() as fh:
        for row in csv.DictReader(fh):
            key = (row["asset_id"], row["date"])
            date.fromisoformat(row["date"])
            if key in quotes:
                raise ValueError(f"Duplicate raw quote: {key}")
            value = float(row["close"])
            if not math.isfinite(value) or value < 0:
                raise ValueError("Raw closes must be finite and non-negative")
            if row["basis"] != "unadjusted" or row["currency"] != "INR" or not row.get("source"):
                raise ValueError("Quotes require unadjusted INR prices and a published source")
            quotes[key] = {**row, "close": value}
    return quotes


def value_position(event, entry, exit_date, quotes, reconciliation=None):
    date.fromisoformat(entry)
    date.fromisoformat(exit_date)
    if entry >= event.get("ex_date", event["impact_start"]) or exit_date < entry:
        return {"status": "unsupported_window", "reason": "Use a pre-ex-date original-parent holding"}
    if not event.get("terms_verified") or not event.get("terms_source"):
        return {"status": "incomplete", "reason": "Unverified entitlement terms"}
    parent = event["parent_asset_id"]
    assets = [{"asset_id": parent, "ratio": 1, "listing_date": None, "is_parent": True}]
    if exit_date >= event["ex_date"]:
        assets += event["entitlements"]
    first = quotes.get((parent, entry))
    if not first or not math.isfinite(first["close"]) or first["close"] <= 0:
        return {"status": "incomplete", "reason": "Missing original-parent unadjusted entry quote"}
    if first.get("basis") != "unadjusted" or first.get("currency") != "INR" or not first.get("source"):
        raise ValueError("Adjusted or unsourced entry prices cannot value an entitlement")
    missing, values = [], []
    for asset in assets:
        if not isinstance(asset["ratio"], (int, float)) or not math.isfinite(asset["ratio"]) or asset["ratio"] <= 0:
            raise ValueError("Entitlement ratios must be positive and finite")
        identity = asset["asset_id"]
        if not asset.get("is_parent"):
            if asset.get("listing_date") is None or not asset.get("listing_source") or exit_date < asset["listing_date"]:
                missing.append({"asset_id": identity, "reason": "No verified listing on or before the valuation date"})
                continue
        quote = quotes.get((identity, exit_date))
        if not quote:
            missing.append({"asset_id": identity, "reason": "Missing same-date raw exit quote"})
            continue
        if quote.get("basis") != "unadjusted" or quote.get("currency") != "INR" or not quote.get("source"):
            raise ValueError("Adjusted or unsourced quotes cannot value an entitlement")
        if not math.isfinite(quote["close"]) or quote["close"] < 0:
            raise ValueError("Exit quotes must be finite and non-negative")
        values.append({"asset_id": identity, "shares_per_original_share": asset["ratio"],
                       "close": quote["close"], "value": quote["close"] * asset["ratio"], "source": quote["source"]})
    # Other raw-basis splits/dividends must be accounted for separately. The
    # caller must provide a sourced cash adjustment, not silently assume none.
    review = reconciliation or {}
    cash = review.get("cash_per_original_share")
    reconciled = (review.get("entry_date") == entry and review.get("exit_date") == exit_date and
                  review.get("no_other_share_changes") is True and bool(review.get("source")) and
                  isinstance(cash, (int, float)) and math.isfinite(cash) and cash >= 0)
    if not reconciled:
        missing.append({"asset_id": parent, "reason": "Cash distributions/splits across the holding window have not been reconciled"})
    if missing:
        return {"status": "incomplete", "reason": "Entitlement wealth cannot yet be valued", "missing": missing, "known_components": values}
    wealth = sum(v["value"] for v in values) + cash
    return {"status": "complete", "entry_value": first["close"], "exit_wealth": wealth,
            "gross_return": wealth / first["close"] - 1, "components": values, "cash": cash,
            "terms_source": event["terms_source"]}


def audit(events=None):
    events = load_events() if events is None else events
    return [{"id": e["id"], "symbols": e["symbols"], "terms_verified": e.get("terms_verified", False),
             "terms_source": e.get("terms_source", e["source"]), "ex_date": e.get("ex_date"),
             "record_date": e.get("record_date"), "entitlements": e.get("entitlements", []),
             "valuation_status": e.get("valuation_status", "unresolved"),
             "return_valuation_ready": False} for e in events]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quotes", help="CSV: asset_id,date,close,basis,currency,source")
    parser.add_argument("--event")
    parser.add_argument("--entry")
    parser.add_argument("--exit")
    parser.add_argument("--reconciliation", help="Sourced, exact-window cash/share-basis review JSON")
    args = parser.parse_args()
    if args.quotes:
        if not all([args.event, args.entry, args.exit]):
            parser.error("quotes require --event, --entry and --exit")
        event = next(e for e in load_events() if e["id"] == args.event)
        review = json.loads(Path(args.reconciliation).read_text()) if args.reconciliation else None
        print(json.dumps(value_position(event, args.entry, args.exit, read_quotes(args.quotes), review), indent=2))
    else:
        print(json.dumps(audit(), indent=2))


if __name__ == "__main__":
    main()
