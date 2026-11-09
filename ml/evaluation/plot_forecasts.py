"""Standalone calibration diagnostics; never depicts a live profit forecast."""

import argparse
import json
from pathlib import Path


def main():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    args = parser.parse_args()
    report = json.loads(args.artifact.read_text())
    fig, axes = plt.subplots(1, 2, figsize=(11, 5), constrained_layout=True)
    for horizon, color in [("21", "#326ac8"), ("63", "#ba6531")]:
        item = report["horizons"][horizon]
        metric = item["probability_metrics"]["profit"]
        bins = metric["reliability"]
        axes[0].plot([r["predicted"] * 100 for r in bins], [r["observed"] * 100 for r in bins],
                     "o-", color=color, label=f"{horizon} sessions (check {'passed' if metric['passed'] else 'failed'})")
        coverage = item["range_metrics"]["observed_coverage"]
        axes[1].bar(horizon, coverage * 100, color=color)
        axes[1].text(horizon, coverage * 100 + 1, f"{coverage:.1%}", ha="center")
    axes[0].plot([0, 100], [0, 100], "--", color="#777777", label="Ideal calibration")
    axes[0].set(xlabel="Predicted scenario-profit frequency (%)", ylabel="Observed frequency (%)", xlim=(0, 100), ylim=(0, 100), title="Later-date probability calibration")
    axes[0].legend(fontsize=8)
    axes[1].axhline(80, linestyle="--", color="#777777", label="Nominal 80% coverage")
    axes[1].set(xlabel="Holding window (sessions)", ylabel="Cases inside estimated band (%)", ylim=(0, 100), title="Later-date net-return band coverage")
    axes[1].legend(fontsize=8)
    for axis in axes:
        axis.grid(axis="y", alpha=.2)
        axis.set_axisbelow(True)
    fig.suptitle("Research diagnostics — no live forecasts approved", fontsize=14)
    fig.supxlabel("8% stop · 10 bps fees + 10 bps slippage per side · overlapping cases are not independent trials", fontsize=9)
    for suffix in ("png", "svg"):
        target = args.artifact.parent / f"diagnostics.{suffix}"
        fig.savefig(target, dpi=160)
        print(target)
    plt.close(fig)


if __name__ == "__main__":
    main()
