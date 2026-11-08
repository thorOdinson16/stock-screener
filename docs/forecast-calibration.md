# Forecast Lab

Forecast Lab adds historical probability and return-band diagnostics, a guarded
forecast API, and explicit corporate-action entitlement accounting. It does not
promote a model or enable unsupported live probabilities. Existing production
rankings remain unchanged.

## Actual results

The run in `ml/evaluation/results/forecast_lab/` reuses the earlier walk-forward
21- and 63-session ranking experiments. These are research horizons; the served
5/21-session models are not silently replaced.

| Horizon | Later completed eligible cases | Profit Brier | Constant baseline | 80% band coverage | Historical band check |
|---|---:|---:|---:|---:|---|
| 21 sessions | 4,565 | 0.2458 | 0.2427 | 85.95% | Passed |
| 63 sessions | 3,727 | 0.2244 | 0.2185 | 84.70% | Failed: too few independent date blocks |

Lower Brier is better: both probability models performed worse than predicting
the fit-period profit frequency for every case. Calibration error was about
6.8 and 9.5 percentage points. Neither supports a live confidence claim.
The 21-session band passed the historical checks, but remains research only:
adjusted execution prices, incomplete corporate-action coverage, and a history
already explored in earlier research prevent production promotion.

## What is measured

Each eligible historical top-20 case enters at the next session close, uses an
8% stop, and otherwise exits after 21 or 63 sessions. Costs are 10 basis points
fees plus 10 basis points slippage per side, with no flat fee. A stop gap fills
at the worse of the opening price and stop price. Missing prices and unresolved
entitlements stay unavailable. A case must reach its scheduled maturity before
calibration, including cases whose stop exited early.

Profit means positive return after those costs. A separate target measures
outperformance of the same-date gross universe return and requires benchmark
availability. Bands describe net proceeds under this exit policy. Multiplying
them by an entry price gives illustrative net exit-value equivalents, not a
prediction of the stock's closing price at the horizon.

Folds 1–2 fit fixed-penalty logistic models using rank percentile and volatility.
Fold 3 calibrates rank-group return bands. Only outcomes whose scheduled exits
precede the next stage are admitted. Folds 4–5 assess the estimates. Each signal
date has equal total weight; stock cases are not independent observations.
Brier improvement intervals use contiguous horizon-length date blocks and
500 seeded bootstrap samples.

The engineering gates require eight assessment blocks, an upper 95% Brier
difference below zero, calibration error at most five percentage points, and
at most 2% missing matured eligible outcomes. Outperformance additionally needs
98% benchmark availability. Band gates require 75–90% observed coverage for an
80% interval, average width at most 75 percentage points, and median absolute
error no worse than a constant median. These thresholds are protocol choices,
not guarantees of future performance or formal coverage guarantees.

## Corporate actions

The registry links verified Vedanta and HEG entitlement terms to published
sources. Terms verification alone cannot establish a holding's return. The
accountant requires asset identities, same-date unadjusted INR prices, verified
child listing dates and sources, and an exact-window reconciliation of cash and
other share changes. Later listing prices cannot value an earlier unlisted
entitlement. Ticker reuse cannot identify a security.

`ml/evaluation/entitlements.py` accepts `--quotes`, `--event`, `--entry`, `--exit`
and `--reconciliation`. Quote CSV columns are `asset_id,date,close,basis,currency,source`;
basis must be `unadjusted`, currency `INR`. Reconciliation JSON needs matching
`entry_date`, `exit_date`, a sourced `cash_per_original_share`, and
`no_other_share_changes: true`. Missing evidence returns an incomplete result.
Raw quote retrieval was unsuccessful in this run; entitlement return valuation
remains pending. See `config/corporate_actions.json` for primary source links.

## Reproduce and inspect

With HDFS available and the repository environments installed:

```bash
bash scripts/calibrate.sh ml/evaluation/results/retail_research_20261108
```

The script audits the fixed research snapshot, writes `outcomes.csv` and
`outcome_audit.json`, fits `calibration.json`, and writes `summary.md` plus
diagnostic PNG/SVG files when matplotlib is available. It archives superseded
calibration artifacts. Snapshot, outcome, policy, event-window and algorithm
hashes protect reproducibility. Changed source evidence requires a new audit.

`GET /api/forecasts/report` serves diagnostics; `GET /api/forecasts/{symbol}?horizon=21`
serves a guarded card. Research status, stale evidence, model lineage, unsupported
policy, ineligibility or missing context withhold numeric live forecasts. Profit
probability, outperformance probability and bands have separate evidence gates.
The dashboard exposes these reasons in Forecast Lab and Trade Planner.

Tests cover chronology, scheduled-maturity purging, costs and gap stops,
unlisted entitlements, reconciliation, calibration against a baseline, and API
withholding. Strong synthetic data passes the probability checks, confirming
that real-data failures are evidence-driven.
