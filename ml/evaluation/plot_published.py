"""Render the historical replay's monthly excess returns as a standalone figure."""

import argparse
import json
from pathlib import Path


def main():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    report = json.loads(args.report.read_text())
    fig, axes = plt.subplots(2, 1, figsize=(12, 8), constrained_layout=True)
    for horizon, axis in zip((5, 21), axes):
        months = report["summaries"][str(horizon)]["months"]
        excess = [(m["net_return"] - m["universe_return"]) * 100 for m in months]
        axis.bar([m["month"] for m in months], excess,
                 color=["#238b57" if x >= 0 else "#ba4141" for x in excess])
        axis.axhline(0, color="#444444", linewidth=.8)
        axis.set_title(f"{horizon}-session holding window: average top-20 net return minus universe gross return")
        axis.set_ylabel("Percentage points")
        axis.tick_params(axis="x", rotation=60, labelsize=8)
        axis.grid(axis="y", alpha=.2)
        axis.set_axisbelow(True)
    provisional = report.get("quality_audit", {}).get("status") == "provisional"
    fig.suptitle(("PROVISIONAL · " if provisional else "") + "Historical replay · next-session entry · 10 bps per side", fontsize=14)
    footnote = "Entry month · overlapping cohorts averaged, not compounded · retrospective selection bias remains"
    if provisional:
        footnote += "\nCorporate-action entitlements missing: these are price-series results, not validated investment returns"
    fig.supxlabel(footnote, fontsize=9)
    for suffix in ("png", "svg"):
        target = args.report.parent / f"monthly_excess.{suffix}"
        fig.savefig(target, dpi=160)
        print(target)
    plt.close(fig)


if __name__ == "__main__":
    main()
