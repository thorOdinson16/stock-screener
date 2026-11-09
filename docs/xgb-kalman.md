# XGBoost and Kalman extension

This is additional historical research on the existing 21-session experiment.
The already examined assessment period is not an untouched holdout. No live
model, screener ranking, frozen paper candidate or trading policy is changed.

## Experiment fixed before fitting

- Input: `tournament_inputs_20261108`, with its checked manifest, original
  outcomes and current-model reference predictions. Prices end on 2026-10-26.
- Validation: original folds 1–3, 2024-04-05 through 2025-10-14.
- Assessment: original folds 4–5, 2025-10-15 through 2026-10-26.
- Training: expanding chronological windows with the original 63-session embargo;
  every training outcome's scheduled maturity must precede the test fold.
- XGBoost: CPU-only version 2.1.4, technical features only, original excess-return
  target and separately the fixed execution-policy return target. Daily labels
  become ten ordinal relevance bins, as in the LightGBM ranker. `rank:ndcg` uses
  linear relevance gains, top-20 pairing, learning rate 0.04, minimum child weight
  100 and L2 regularization 10. Two fixed configurations: depth 3 / 100 trees,
  depth 4 / 150 trees. Each signal date gets equal query weight. No early stopping.
- For each target, choose the configuration with the highest validation mean
  daily Spearman rank correlation; lower configuration index breaks ties.
- Controls: existing price-only LightGBM rankers for both targets, current tree
  ensemble reference and matched 63-session momentum.
- Every strategy receives the same stock/date inference keys, selected using
  past price availability alone. Missing future outcomes never filter the pool.
- Production-style evaluation: INR100,000, ten positions, whole shares,
  liquidity and exposure limits, 8% stops, fees and slippage each 10 basis points
  per side. Signals enter at the next session close. Stress scenarios double
  costs or delay entry by another session. Equal-notional, nonoverlapping
  21-session baskets assess stock selection separately from allocation.
- Candidate selection uses only validation and the existing execution-target,
  rank-consistency, drawdown, trade-count and three-scenario momentum gates.
  Later assessment is run only after that selection is written.

The [official XGBoost API documentation](https://xgboost.readthedocs.io/en/latest/python/python_api.html#xgboost.XGBRanker.fit)
explains that ranking weights apply to query groups, rather than individual
rows. The installed 2.1.4 implementation is exercised by a real-fit test.
The optional CPU wheel stays in `ml/.venv`; Spark, API and poller dependencies
are unchanged.

## Kalman equation

Use a forward local-linear-trend state, x = [log-price level, daily slope], with
transition F = [[1,1],[0,1]] and observation H = [1,0]. At each available close:

```
x_prior = F x_previous
P_prior = F P_previous F' + Q
K = P_prior H' / (H P_prior H' + R)
x = x_prior + K (log(close) - H x_prior)
P = (I-KH) P_prior (I-KH)' + K R K'
score = 21 * slope / sqrt(21 * trailing_return_variance63 + 21^2 * P[slope,slope])
```

Fixed R = 1e-4 and Q = diag(1e-6, 1e-8). Initial level is the first available
log close, initial slope zero, initial P = diag(R, R/100). There is no backward
smoothing, parameter fitting or window search. The score is a ranking heuristic;
the denominator is not a calibrated prediction interval or profit probability.

Require 253 consecutive common-calendar closes before scoring. A missing,
nonpositive or nonfinite price, or a registered corporate-action impact date,
resets the state and warmup. No price filling or calendar compression is used.
Kalman consumes longer raw history than the tabular models' existing technical
features, so their comparison tests strategies rather than isolating architecture
under exactly equal information.

## Statistical interpretation

The three new hypotheses are XGBoost with each target and the fixed Kalman rule.
Paired independent-cycle return differences against momentum use 5,000 seeded
bootstrap draws and Bonferroni-adjusted quantiles across those three hypotheses.
At least eight complete cycles, complete diagnostics, a positive lower interval,
and positive retail outperformance in all three scenarios are required for the
support flag. This descriptive check does not erase earlier project-wide tests
or dependence across market regimes. Production promotion remains disabled.

The fixed common universe and industry taxonomy, provider-adjusted execution
prices, unresolved entitlement valuations and simplified fills remain material
limitations. Incomplete portfolios must not be presented as full-period returns.

## Measured results

The common pool contains 281,849 stock/date rows, 630 signal dates and 479
stocks, retaining 98.27% of the original reference inference rows. All strategies
use this same pool. The matched retail momentum control returned -16.30% in
validation and +34.70% in later assessment. Returns below are cumulative after
modeled costs, not annualized rates.

| Candidate | Validation net | Later net | Later drawdown | Double costs | Extra entry delay |
|---|---:|---:|---:|---:|---:|
| XGBoost, execution target | +1.45% | -9.57% | -10.81% | -10.96% | -9.76% |
| XGBoost, original target | -1.58% | -0.45% | -7.37% | -4.56% | +5.44% |
| Kalman trend | -3.21% | Incomplete | Unavailable | Incomplete | Incomplete |
| Existing price-only LightGBM, execution target | -3.11% | -9.41% | -10.29% | -9.78% | -2.48% |
| Existing price-only LightGBM, original target | +3.54% | +11.19% | -16.93% | +7.25% | +14.32% |
| Current ensemble reference | +28.00% | Incomplete | Unavailable | Incomplete | Incomplete |

Both XGBoost targets selected depth 3 / 100 trees on validation rank correlation.
The execution target had positive validation daily rank IC in all three folds
(0.0223, 0.0220, 0.0504) and passed the existing paper-selection gate. The
original target had negative IC in all three folds and did not qualify. Kalman
had positive IC in only one fold (0.0342, -0.0163, -0.0753), failing consistency.
The validation gate therefore selected execution-target XGBoost before later
assessment. It subsequently lost money and underperformed matched momentum in
all three execution scenarios. There is no supported replacement.

Kalman and the current-model reference portfolios stop with an unvalued
`heg-demerger-2026` entitlement for `HEGAM.NS` on 2026-09-01. Their earlier
partial paths are not full-period return estimates. The all-eligible equal-weight
basket is also incomplete; missing valuations remain visible rather than dropped.

Equal-notional stock-selection diagnostics tell the same broad story. Over
11 independent complete cycles, execution-target XGBoost returned -3.16%, the
original-target XGBoost +7.85%, and matched momentum +25.24%. Adjusted intervals
for the average cycle return difference versus momentum were:

| New candidate | Complete paired cycles | Mean difference | Adjusted interval |
|---|---:|---:|---:|
| XGBoost, execution | 11 | -2.47 percentage points | [-6.15, +1.21] percentage points |
| XGBoost, original | 11 | -1.29 percentage points | [-4.20, +1.89] percentage points |
| Kalman | 10 | -1.49 percentage points | [-3.64, +0.28] percentage points |

All intervals include zero. Kalman's full diagnostic is additionally incomplete.
No candidate meets the assessment support gate. No further parameter search,
new blend or retrospective paper-candidate replacement was made.

Validation checked 16 model fits and their prediction hashes, with every training
maturity strictly before its test start. Ten tests cover causal filtering,
calendar/event resets, scale invariance, invalid inputs, real grouped ranker
fitting, maturity purging and rejection of incomplete or cost-sensitive evidence.

## Reproduce

```bash
ml/.venv/bin/pip install -r ml/requirements-xgb.txt
bash scripts/xgb_kalman.sh
PYTHONPATH=tests ml/.venv/bin/python -m unittest test_xgb_kalman
```

The script uses local frozen snapshots; it does not start HDFS or other services.
Optional positional arguments specify input snapshot, base tournament and output
directory. A completed run is frozen; use a new output path for a new protocol.
Artifacts are ignored runtime output in `ml/evaluation/results/xgb_kalman_20261109/`:
protocol, validation selection, checkpointed predictions with hashes and maturity
receipts, detailed simulations, assessment signals, report and summary.
