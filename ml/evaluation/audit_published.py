"""Attach a transparent corporate-action/data-quality audit to the replay.

Event sources were verified during the initial backtest review. No returns are
adjusted or removed: price-only results remain explicitly provisional.
"""

import argparse
import hashlib
import json
from pathlib import Path

EVENTS = [
    {"symbol": "HEGAM.NS", "date": "2026-09-07", "event": "HEG demerger record date",
     "source": "https://www.hegadvancedmaterials.com/media/press-releases/heg-demerger-to-take-effect-from-september-1-2026-record-date-fixed-for-september-7"},
    {"symbol": "VEDL.NS", "date": "2026-04-30", "event": "Vedanta demerger ex-date",
     "source": "https://nsearchives.nseindia.com/content/circulars/FAOP73857.pdf"},
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    report = json.loads(args.report.read_text())
    affected = []
    for cohort in report["cohorts"]:
        if not cohort["entry_date"] or not cohort["exit_date"]:
            continue
        for pick in cohort["picks"]:
            for event in EVENTS:
                if pick["symbol"] == event["symbol"] and cohort["entry_date"] < event["date"] <= cohort["exit_date"]:
                    affected.append({"cohort_id": cohort["id"], "horizon": cohort["horizon"],
                                     "entry": cohort["entry_date"], "exit": cohort["exit_date"],
                                     "gross_price_return": pick["gross_return"], **event})
    audit = {"status": "provisional", "verified_events": EVENTS, "affected_pick_windows": affected,
             "note": "Price-only returns omit resulting share entitlements across these demergers. No estimated adjustments or exclusions were applied. Other corporate actions may also be present; this event list is not exhaustive.",
             "audit_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    (args.report.parent / "quality_audit.json").write_text(json.dumps(audit, indent=2, allow_nan=False))
    report["quality_audit"] = audit
    caveat = "PROVISIONAL: verified HEG and Vedanta demergers fall within evaluated windows; the price-only series do not value resulting share entitlements. Apparent losses and portfolio/benchmark returns are not validated total investment returns. See quality_audit.json."
    if caveat not in report["caveats"]:
        report["caveats"].insert(0, caveat)
    args.report.write_text(json.dumps(report, indent=2, allow_nan=False))
    from backtest_published import write_markdown
    write_markdown(report, args.report.parent)
    summary = args.report.parent / "summary.md"
    content = summary.read_text()
    first, rest = content.split("\n", 1)
    summary.write_text(first + "\n\n**Status: provisional. Corporate-action entitlements are missing from price-only returns; do not interpret these as validated investment returns.**\n" + rest)
    with summary.open("a") as fh:
        fh.write("\n## Verified corporate-action audit\n\n")
        for event in EVENTS:
            fh.write(f"- {event['symbol']}: {event['event']} ({event['date']}). [Primary source]({event['source']}).\n")
        fh.write(f"\n{len(affected)} selected pick/holding-window combinations cross these events. No losses were removed or guessed away.\n")
    print(f"PROVISIONAL: {len(affected)} selected pick windows cross verified demergers; see {summary}")


if __name__ == "__main__":
    main()
