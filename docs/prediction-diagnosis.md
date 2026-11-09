# Why the forecasts fail: frozen-prediction diagnosis

This report diagnoses existing predictions. It does not train a model, search
parameters, pick a sector filter, change allocation rules or promote a strategy.
It is retrospective analysis of already examined history, not fresh evidence.

## What is measured

Seven strategies share the XGBoost/Kalman experiment's common pool: the current
ensemble reference, price-only LightGBM with each target, XGBoost with each target,
Kalman trend and matched 63-session momentum. Validation is 2024-04-05 through
2025-10-14; later assessment is 2025-10-15 through 2026-10-26. Ranking statistics
use the same signal-time liquidity, volatility and corporate-action eligibility
rules. Future outcome availability never determines eligibility or selection.
The original base portfolio must reproduce exactly before its attribution is
accepted. No new models are fitted, including for the zero-cost scenario.

**Ranking:** mean daily Spearman correlation against the execution-policy net
return and, separately, the original excess-return target. Positive correlation
means higher-ranked stocks subsequently did better on that target. Outcomes must
mature by the end of their validation/assessment period. Known-label fractions,
dates and sample sizes are reported; partial-label correlations are conditional
on those known labels. They are not complete-universe estimates.

The top 20 are chosen before inspecting labels. A missing selected outcome makes
that date's top-20 return unavailable. The top-20 minus eligible-pool spread is
available only when every required outcome is known. These daily forward returns
overlap heavily; they are descriptive and must not be compounded as a portfolio.

**Time and sector:** fixed calendar quarters and the existing industry taxonomy.
Overall and quarterly IC requires at least 20 known stocks per date; within-sector
IC requires five. Sector groups with fewer than 20 evaluable dates are explicitly
flagged as small samples. Within small sectors, the up-to-20 selection can include
the entire sector. No sector is selected for future trading using these results.

**Market conditions:** signal-date NIFTY 50 trailing 63-session return sign and
20-session volatility above/below a median fixed from data preceding validation.
Both conditioning variables use only past prices. Portfolio market exposure,
cash, beta and calendar-quarter returns are reported separately. These are
historical associations, not proof of what caused a model to fail.

**Costs:** reconstruct gross P&L for the exact same closed trades, quantities and
entry/exit reference prices, then reconcile it with net P&L. The difference is
direct modeled fees and slippage. A separate zero-cost re-simulation shows the
broader execution sensitivity; removing costs changes sizing and may change
holdings, so that return difference is not a pure fee attribution.

**Allocation:** compare top-20 equal-notional baskets with constrained equal-target
baskets on the exact same nonoverlapping schedule and ranked stock lists. Report
cash/exposure and incomplete outcomes in both. The daily ten-position planner is
shown separately: its difference from the basket also includes timing, position
count and reuse of capital, not just sizing.

**Missing data:** incomplete portfolios retain their blocked reason and expose
only a marked prefix and closed-trade contributions. They never receive an
asserted full-period return. Sector and exit-quarter P&L sums cover closed trades;
calendar-quarter portfolio returns use marked daily equity instead.

## Main finding: full-ranking skill did not translate to top-20 skill

The execution-target models did not simply lose all ranking correlation. On
231 later signal dates with evaluable labels, execution-target XGBoost's mean
daily rank IC was +0.0545, compared with momentum's +0.0497. Yet its highest-ranked
stocks were poor buys under the trading policy. The daily top-20 spread below
uses the same 193 dates on which the entire eligible pool has known outcomes.
These are average forward-return spreads, not portfolio returns.

| Strategy | Validation execution IC | Later execution IC | Later original-target IC | Later top-20 minus eligible pool |
|---|---:|---:|---:|---:|
| XGBoost, execution target | +0.0309 | +0.0545 | -0.0174 | -0.275 percentage points |
| LightGBM, execution target | +0.0181 | +0.0493 | -0.0156 | -0.244 percentage points |
| XGBoost, original target | -0.0904 | +0.0377 | +0.0679 | +0.768 percentage points |
| LightGBM, original target | -0.0675 | +0.0134 | +0.0527 | +1.206 percentage points |
| Momentum | -0.0450 | +0.0497 | +0.0381 | +2.138 percentage points |

This corrects an overly broad explanation that every failure means the model
learned no ranking signal. Whole-universe rank correlation is not the objective
of a top-20 buying strategy. Selection by validation mean IC can reward useful
ordering elsewhere in the distribution while missing the winning tail. Agreement
with the stop-policy target also does not imply agreement with the original
excess-return target. These are measured mismatches, not a claim that changing
one metric alone will fix returns.

One possible mechanism for a future experiment is target resolution: the
rankers use ten relevance bins, so stocks within the top decile share a label.
That objective cannot fully distinguish the roughly 20 stocks to buy within a
larger top decile. This is a hypothesis from the implementation, not a causal
explanation established by this diagnosis.

## Time, sectors and market conditions

The market pattern changed materially: momentum's retail result shifted from
-16.30% in validation to +34.70% later. The later quarter comparison shows where
much of the gap emerged, rather than attributing every loss to a generic regime:

| Portfolio calendar quarter | Execution-target XGBoost | Momentum |
|---|---:|---:|
| 2025 Q4, from assessment start | -1.50% | +6.27% |
| 2026 Q1 | -8.94% | -3.66% |
| 2026 Q2 | +4.41% | +27.75% |
| 2026 Q3 | -2.84% | +3.40% |
| 2026 Q4, through assessment end | -0.61% | -0.39% |

The execution-target XGBoost signal had positive sector IC in 14 of 18 sectors
with sufficient daily cross-sections, yet its actual closed trades lost money in
15 of the 19 sectors it traded. Its largest sector P&L losses were FMCG
(-INR1,723), IT (-INR1,489) and oil/gas/fuels (-INR1,400). Overall or within-sector
correlation is therefore not enough to establish good absolute buys or good
cross-sector capital allocation. Sector correlations and P&L answer different
questions; no sector exclusion was selected after inspecting these results.

Execution-target XGBoost's signal-date IC was +0.0883 when the market's trailing
63-session return was positive, versus +0.0140 when negative. Momentum was
+0.0647 and +0.0317 respectively. The fact that XGBoost can have higher IC in an
up-trend slice while earning less again points to the top-tail/portfolio mismatch,
not a ready-made market-regime trading rule.

Momentum's strong result is concentrated too: five stocks supplied 69.31% of its
positive closed-trade P&L. This does not invalidate its measured advantage, but it
limits claims of a broadly reliable edge. Industry membership is today's taxonomy.

## Allocation findings

These first two columns share the same selected top-20 lists and 11 independent
complete cycles. The daily ten-position planner changes timing and capital reuse
as well as sizing, so its difference is not attributed solely to allocation.

| Strategy | Equal-notional top20 | Constrained equal-target top20 | Daily ten-position planner |
|---|---:|---:|---:|
| XGBoost, execution | -3.16% | -1.22% | -9.57% |
| XGBoost, original | +7.85% | +6.00% | -0.45% |
| Momentum | +25.24% | +16.56% | +34.70% |

Execution-target XGBoost already loses in the fractional equal-notional basket.
Portfolio allocation is not its sole failure. Original-target XGBoost has some
positive selection performance, but loses much of it in constrained/daily
execution. Momentum's advantage survives the same-schedule stock-selection test.

Later mean cash fractions were 48.42% for execution-target XGBoost, 48.14% for
original-target XGBoost and 47.02% for momentum. Cash alone does not explain their
large return differences. Both constraints and the actual selected/held stocks
matter. In a separate zero-cost re-simulation, execution-target XGBoost still
lost 5.89%; original-target XGBoost earned 7.01%; momentum earned 43.30%.

## What to change next

Prioritize a predeclared top-20 economic selection criterion and stability across
chronological periods, alongside IC as a diagnostic. Explicitly test tail-label
resolution and target choice under the same execution policy. Then isolate
portfolio scheduling and turnover for a candidate that first demonstrates useful
stock selection. These are proposed experiments, not validated improvements.
Do not infer a sector filter, regime switch or winning replacement from these
already inspected slices. Collect new, frozen prospective outcomes before
claiming an improvement.

## Trading-cost findings

The later period starts with INR100,000. The following bridge holds the actual
closed trades, quantities and reference fills fixed; it isolates modeled direct
fees and slippage without changing holdings.

| Model | Gross P&L before costs | Direct cost drag | Net P&L |
|---|---:|---:|---:|
| XGBoost, execution target | -6,853.29 | 2,717.72 | -9,571.01 |
| XGBoost, original target | +2,833.65 | 3,283.17 | -449.52 |
| LightGBM, execution target | -6,732.64 | 2,676.02 | -9,408.66 |
| LightGBM, original target | +14,843.42 | 3,650.59 | +11,192.83 |

Execution-target XGBoost and LightGBM lost money even before costs. Costs cannot
be their sole failure explanation. Original-target XGBoost's positive gross
result was smaller than its direct cost drag. Cost sensitivity is a different
problem in that case. The existing original-target LightGBM remained profitable
after costs, but still lagged its matched momentum control.

## Reproduce and inspect

```bash
bash scripts/diagnose_predictions.sh
PYTHONPATH=tests ml/.venv/bin/python -m unittest test_prediction_diagnosis
```

The isolated ML environment and the existing frozen XGBoost/Kalman research run
are required. HDFS and the live stack are not started. Optional positional
arguments select research and output directories. Completed diagnoses are frozen.

`ml/evaluation/results/prediction_diagnosis_20261109/report.json` contains source
and input hashes, daily ranking metrics, quarter/sector/regime summaries, gross
and net accounting, zero-cost portfolios, exposure, turnover, stops, concentration,
and same-schedule allocation comparisons. Five tests check target disagreement,
equal date weights, unknown selected outcomes, small cross-sections, exact cost
reconciliation, attribution totals and incomplete-book handling.

Historical selection bias, current-universe survivorship, current industry
membership, adjusted execution prices and corporate-action valuation gaps still
apply. Many correlated slices are being inspected. This diagnosis supports
follow-up hypotheses; it does not establish a sector-specific or regime-specific
trading edge.
