# Verified financial pilot experiment — 9 November 2026

The controlled comparison is complete. **No candidate qualifies for advancement.**
The financial model lost less in the later retail simulation, but did not show a
consistent stock-selection improvement. Production and the existing frozen paper
trial are unchanged. This result uses previously examined history, not a fresh
holdout or prospective outcome.

## Frozen design

The data-readiness gate verified all 360 required stock-quarter cells and all 695
audited signal dates before training. The experiment then froze its protocol and
source hashes in `ml/evaluation/results/verified_pilot_experiment_20261109/protocol.json`.
No parameters or thresholds were changed after viewing results.

Three feature sets use the same existing LightGBM model: technical only;
technical plus sector context; and those features plus verified financial
margins, quarter/year revenue changes, profit changes, margin changes and filing
age. The model has 7 leaves, 100 trees, learning rate .04, minimum child size 100,
L2 regularization 10 and seed 42. There is no tuning search. Median imputation and
missing indicators are fitted on training rows only.

All models train on the frozen 20-stock pilot from January 2022. The five original
chronological folds are retained, with a 63-session embargo and a further explicit
check that each training outcome matured before its test period. Training rows
range from 7,140 in fold 1 to 17,220 in fold 5. The least-covered financial feature
has 3,196 known training rows in fold 1, rising to 13,276 in fold 5. This test no
longer has the preceding experiment's near-empty earnings-change training data.

Sector features are computed from the wider original universe before restricting
to the pilot. Financial inputs use original publication plus the next calendar
day, with prior-quarter/year reports already public by the feature date. They
are historical reconstructions, not a contemporaneously observed archive.

The existing model's recorded scores and simple momentum are additional controls.
Every strategy gets identical scored stock/date keys and signal-time eligibility.
The average eligible pool is about 19.9 stocks. Outcomes do not determine which
stocks can be selected. Unknown selected outcomes remain incomplete.

Stock selection uses equal-notional **top-five** baskets. Selecting all 20 would
not test ranking differences. The separate executable diagnostic uses the
existing ₹100,000 ten-position planner, whole shares, position/sector/risk limits,
next-session-close entry, an 8% stop, and 10bps fees plus 10bps slippage per side.
Double-cost and one-session-delayed entry scenarios are fixed in advance.

Three validation folds cover 5 April 2024–14 October 2025. The validation decision
was saved before evaluating the later two folds, 15 October 2025–23 October 2026.
Neither period is newly untouched evidence. The top-five cycle schedule is
chronological and nonoverlapping: 17 complete validation cycles and 11 complete
later-assessment cycles, with 100% selected-outcome coverage in each scenario.

## Actual results

Net retail portfolio returns for each entire period, after the base costs:

| Strategy | Validation | Later assessment |
|---|---:|---:|
| Technical | +0.94% | −1.99% |
| Technical + sector | −4.94% | −4.22% |
| Technical + sector + verified financials | +0.86% | −1.00% |
| Existing model, same pilot pool | −0.75% | −6.64% |
| Momentum, same pilot pool | −4.70% | −2.59% |

These are period returns, not annualized returns. The books hold roughly half
their equity as cash on average. No cash interest is credited. All positions are
closed at these period endpoints; the general retail simulator can otherwise
include marked open positions, which are reported separately.

The later financial-model retail drawdown is 6.82%, versus 8.89% for the sector
control. This isolated improvement does not pass the full selection test.

| Financial model diagnostic | Validation | Later assessment |
|---|---:|---:|
| Retail return, double costs | −2.68% | −3.79% |
| Retail return, delayed entry | +0.98% | −3.74% |
| Mean top-five basket return per 21-session cycle | −1.10% | −1.72% |
| Top-five difference versus sector, base costs | +0.21 percentage points | −1.11 percentage points |
| Top-five difference versus momentum, base costs | −1.44 percentage points | −0.95 percentage points |

The validation financial-versus-sector top-five advantage becomes **−0.20
percentage points** with delayed entry. Its descriptive 95% paired interval at
base costs is −0.88 to +1.25 percentage points. The later interval is −2.58 to +0.59
percentage points. Both include zero; these intervals are descriptive and not
adjusted for the many prior research attempts.

The financial model's daily ranking correlations by fold are −0.202,−0.151,
+0.094,+0.004 and−0.043. Only two are positive, one barely so. All five models
and all scenarios remain in the report, including failed comparisons.

## Decision and limits

The frozen advancement rule requires complete positive top-five and retail
advantages over sector, momentum and the existing model under all three scenarios,
at least 8 paired cycles, positive top-five advantages in at least 2 of 3 validation
folds, at least 20 closed retail trades and drawdown no worse than 25%.
The financial candidate fails that rule. Its better later retail loss does not
override the earlier rejection or the weaker top-five selections.

Better coverage removed one data obstacle; it did not establish that these few
financial changes predict 21-session returns reliably. The useful conclusion is
that this fixed feature/model combination does not justify replacement or a new
endorsed candidate. The current prospective trial continues unchanged.

Limits include the small 20-stock sample, current-universe and sector membership,
provider-adjusted prices, unvalued corporate-action entitlements where applicable,
and inherited common-row restrictions from the original tournament. The latter
requires finite legacy feature rows even though the historical annual fundamental
values themselves are not inputs to these new models. Costs, fills and taxes
remain simplified. No architecture search or retrospective production promotion
was performed.

## Reproduce and inspect

```bash
bash scripts/verified_pilot_experiment.sh
```

The command first verifies the financial readiness gate and input/source hashes.
A completed run is frozen; use a new output directory to change its protocol.
Original source snapshots from the pilot and tournament must be present.

Runtime artifacts are under
`ml/evaluation/results/verified_pilot_experiment_20261109/`: frozen `protocol.json`,
validation-only `selection.json`, per-fold prediction/checkpoint receipts,
`features.parquet` and full `report.json`. The report retains selected symbols,
individual outcomes, costs, delayed entries, fold results, missing-outcome
coverage and training maturity receipts. No model weights are saved to
production or attached to the existing paper trial.
