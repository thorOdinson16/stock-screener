# Top-20 targets and economic configuration selection

This experiment follows the [frozen-prediction diagnosis](prediction-diagnosis.md).
It tests whether target resolution and validation configuration selection explain
some of the gap between whole-universe correlation and useful top-20 selections.
It does not change the production model or trading policy.

## Fixed comparison

Use the same technical features, frozen stock/date inference pool, 21-session
execution target, expanding folds, 63-session embargo, training maturity purge,
and trading costs as the XGBoost/Kalman experiment. Validation covers 2024-04-05
through 2025-10-14. Later assessment covers 2025-10-15 through 2026-10-26.
Both periods have already been inspected; this is historical research.

One architecture, CPU XGBoost 2.1.4, receives two fixed configurations: depth 3 /
100 trees and depth 4 / 150 trees. Learning rate 0.04, minimum child weight 100,
L2 regularization 10, four threads and seed 42 remain fixed. No early stopping,
feature search, clipping search, blend or new architecture is introduced.

| Training arm | What changes |
|---|---|
| Coarse rank control | Existing ten-bin daily relevance; cached predictions reused with their hashes and maturity receipts |
| Fine rank | One dense ordinal relevance per distinct daily net outcome, preserving ties; linear-gain NDCG with top-20 pairing |
| Net-return regression | Predict the actual unmodified execution-policy net return using squared error and equal-date training weights |

Fine labels distinguish winners within the prior top-decile bin. They still
represent order, not monetary distances; direct regression tests monetary outcome
magnitude separately. Raw regression can emphasize extreme returns. No claim of
causality or guaranteed improvement follows from changing the target.

Each arm is assessed under both configuration-selection rules, using the same
validation predictions and the same two configurations:

1. Highest mean daily rank IC, the original correlation-based rule.
2. Highest mean paired independent-cycle top-20 net-return excess over momentum.

The second rule uses equal-notional baskets on the original fixed nonoverlapping
schedule, after the fixed stops and costs. Selected stocks with missing outcomes
invalidate the configuration's economic score; no unavailable cycle is dropped.
Lower configuration index breaks a tie. This separates target changes from
selection-rule changes. If both rules choose the same configuration, their results
are intentionally identical, not separate trained models.

## Conservative candidate gate

Only variants selected by the top-20 economic rule can become this experiment's
validation choice. Require all of the following:

- Complete equal-notional comparison, positive cumulative net return and positive
  mean cycle excess over matched momentum.
- Positive mean cycle excess in at least two validation folds, with at least
  three complete cycles in each qualifying fold.
- Positive actual ten-position retail return exceeding momentum under base,
  doubled-cost and extra-session-entry-delay scenarios.
- At least twenty closed validation trades and drawdown no worse than -25%.

Among qualifying variants, highest validation mean cycle excess wins. Freeze the
selection before fitting or evaluating assessment folds. Assessment includes
both selection rules as diagnostics; there is no replacement of the validation
choice using a later winner. The three cost/entry scenarios, equal-notional
selection and constrained portfolios are retained for every selected variant.

The existing five-hypothesis nominal adjustment covers two new target arms under
two rules plus economic selection of the original coarse arm. Paired cycle
bootstrap checks remain descriptive; this adjustment does not account for all
past project experiments or dependence across market regimes. No production
promotion is enabled even if a historical support check passes.

## Actual results

Neither new target qualified on validation. The original coarse control also
failed the stricter positive-return cost/delay gate. The validation choice was
therefore **none**, locked before assessing later dates.

| Target | Validation retail | Validation equal-notional top20 | Later retail | Later doubled costs | Later extra entry delay |
|---|---:|---:|---:|---:|---:|
| Coarse rank | +1.45% | +5.88% | -9.57% | -10.96% | -9.76% |
| Fine rank | +5.18% | -1.04% | -9.20% | -10.52% | -4.28% |
| Direct net-return regression | +5.30% | -10.59% | Incomplete | Incomplete | Incomplete |

The matched momentum control returned +34.70% later. These are cumulative
returns after modeled costs, not annualized figures. The same common inference
pool contains 281,849 rows, 630 signal dates and 479 stocks.

IC and top20_net configuration selection made identical choices for each arm:
configuration 0 (depth 3 / 100 trees) for coarse and fine ranks, configuration 1
(depth 4 / 150 trees) for regression. Their duplicated rows in the machine report
are intentional. Changing the selection rule had no effect within this bounded
grid; that does not prove the two criteria are generally equivalent.

The coarse control's base validation return was positive, but doubled costs and
entry delay each produced a nonpositive retail return. The new gate rejects it
even though it outperforms negative-return validation momentum. Fine rank and
regression had positive retail validation returns but negative equal-notional
selection returns. They failed the positive stock-selection requirement, despite
beating a still worse momentum basket during that period.

Fine-rank retail assessment is complete, but its equal-notional comparison has
an unavailable cycle. Direct regression's later retail book stops on 2026-09-01
with an unvalued `HEGAM.NS` entitlement (`heg-demerger-2026`). Its partial wealth
path is not reported as a full-period result. Missing outcomes were not dropped
to improve either validation or assessment.

All nominal five-hypothesis-adjusted paired-cycle intervals include zero. The
coarse control's mean cycle gap versus momentum was -2.47 percentage points
(11 complete pairs, interval [-6.42,+1.57]); fine rank -2.69 points (10 complete
pairs, [-7.08,+1.25]); regression -0.46 points (10 complete pairs,
[-3.51,+2.26]). The latter two full diagnostics are incomplete, so these known-pair
summaries cannot replace a full comparison. No historical support gate passed.

## Interpretation

This test does not support the hypothesis that coarse tail labels alone explain
the poor retail results. Finer labels changed predictions without establishing a
useful improvement. Direct monetary regression did not establish useful validation
stock selection. Selecting configurations using top-20 economics did not change
the chosen models in this small grid.

The stricter gate is useful as a rejection rule: it prevents treating positive
retail validation returns or outperformance of a losing benchmark as sufficient
stock-selection evidence. It is not evidence that the model itself improved.
Further architecture or parameter searches on these inspected periods would add
selection risk. Prioritize richer trustworthy information and prospective evidence;
these findings do not establish which new information would provide an edge.

The run used 16 new fits and eight reused coarse-control prediction checkpoints.
All 24 maturity receipts and prediction hashes were verified. Eight focused tests
passed. Production models, live rankings and existing frozen paper trials remain
unchanged.

## Reproduce

```bash
bash scripts/top20_experiment.sh
PYTHONPATH=tests ml/.venv/bin/python -m unittest test_top20_experiment
```

Requires the existing isolated ML environment (`ml/requirements-xgb.txt`) and
frozen XGBoost/Kalman snapshots and predictions. No HDFS or live service is started.
Optional positional arguments select research and output paths. Completed runs
are frozen. Artifacts live in `ml/evaluation/results/top20_targets_20261109/`:
protocol, config trials, validation-only choice, prediction hashes and maturity
receipts, detailed simulations, assessment signals, report and summary.

Eight tests check fine-label tail resolution, preservation of ties, date-local
ranks, actual ranking with more than 31 labels, direct regression, distinct metric
choices, invalid/misaligned cycles and the stability/cost rejection gates.
Adjusted prices, current-universe survivorship, current industry membership,
unknown corporate-action valuations and simplified fills remain limitations.
