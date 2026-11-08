# Research plan: implemented experiments and prospective evidence

The remaining executable work is implemented and has been run. Fresh paper
signals have been recorded. **The 21-session prospective assessment remains
pending real market time**; no historical return is substituted for it.
Production model selection, served rankings and warehouse tables are unchanged.

## Plan status

| Component | Evidence/status |
|---|---|
| Freeze protocol and data | Separate extension protocol, source/data hashes, fixed learner configurations and chronology |
| Market/sector context | Eight past-only features, including market returns/trend/volatility, sector momentum and relative strength, and beta |
| Verified financial information | Original NSE filings and exchange publication times; a shorter covered-subset experiment with a matched context-only control |
| Categorical inputs | Native CatBoost industry categories; train-fitted one-hot categories for other learners |
| Execution targets | Net-return ranks, net excess-return regression, profit classification and outperformance classification |
| Sequence architecture | Contextual TCN and an identical-window flattened tree control |
| Forecast calibration | Separate chronological sigmoid calibration for four classifiers; later block/date-weighted assessment |
| Trading usefulness | Equal-notional diagnostics and ten-position retail simulations, doubled costs and delayed entry |
| Robustness | Quarters, market/volatility regimes, beta, cash, turnover, stock/industry contributions and validation-chosen top-stock removal |
| Conservative selection | Validation choice locked before later assessment; an additional explicit advancement audit rejects unsupported candidates |
| Conditional ensembles | Not tested: no two individual candidates qualified for advancement |
| Prospective records | Fresh paired cohort captured; fixed candidate, current-model, TCN and momentum controls; future outcomes pending |

## What was tested

The extension runs eleven experimental arms and 61 fold fits, plus the frozen
validation choice's final refit. It preserves the original five chronological
folds and 63-session embargo for the full-history arms. Separate outperformance
targets compare a stock's net stop-policy return with the equal-notional mean of
the eligible signal-time universe. A benchmark is unavailable if any constituent
outcome is unknown; it is not computed from a conveniently surviving subset.

All numeric imputation/scaling and category encodings fit training rows only.
Sector features use contemporaneous, leave-one-out industry momentum; the
industry taxonomy is still today's taxonomy and is explicitly a historical-data
limitation. Market and beta windows use only prices available through the signal.
The contextual sequence models share exactly the same past windows. Their fixed
missing-value representation includes explicit missing flags; neural scaling
still fits training windows only.

The four classifiers predict either positive net return or outperformance. Their
sigmoid calibrators fit fold-3 out-of-sample scores only when scheduled outcomes
mature before fold 4. Later assessments report raw/calibrated Brier, constant
baseline, reliability, calibration error, missing-target coverage and a
horizon-block interval. These historical estimates do not enable live confidence.

## Verified financial subset

The source is the [NSE financial-results index](https://www.nseindia.com/companies-listing/corporate-filings-financial-results).
The collection retrieved 431 original linked XBRL documents. Identity,
consolidated scope, primary quarterly context, currency, reporting period and
publication evidence passed for **394 filings across 376 companies**. Thirty-seven
documents were rejected, including ambiguous/unmapped contexts and inconsistent
future reporting periods. Original files, SHA-256 digests and timestamp evidence
are retained. Verification concerns provenance and parsing, not an independent
audit of company-reported financial accuracy.

A filing becomes available on the calendar day after exchange dissemination, a
conservative full-day rule. Reporting-period dates are never used as publication
dates. The parser selects the primary reporting quarter; it does not confuse a
same-dated cumulative context with quarterly revenue. Statements older than one
year are excluded from the covered panel.

This corpus supplied verified profit margins and filing age. It supplied **zero
unambiguous comparison-year revenue/profit growth observations**; those inputs
remain missing and provide no information in this run. Existing Yahoo annual
snapshots are not relabelled as verified data.

The matched covered panel has 236 dates from 2025-01-20 to 2025-12-31. Three
expanding folds with a 22-session embargo compare context-only and financial
augmentation on identical observed rows. A separately reported last-fold test
allows its holdings to mature. It returned **2.34% for the context-only control
and 1.94% with verified financial inputs**, versus **8.19% matched momentum**.
That restricted experiment did not demonstrate a financial-feature improvement.
Its shorter corpus is not verified coverage of the entire original five years.

## Historical results and decision

Later full-history assessment remains 2025-10-15 through 2026-10-26. These are
cumulative ten-position portfolio returns after modeled costs, not annual rates.

| Arm | Later net return | Matched momentum |
|---|---:|---:|
| Technical rank control | -5.22% | 33.51% |
| Context LightGBM ranker | -4.38% | 33.51% |
| Context CatBoost ranker with native categories | -1.95% | 33.51% |
| Profit CatBoost classifier | -4.48% | 33.51% |
| Outperformance CatBoost classifier | 6.55% | 33.51% |
| Net excess-return LightGBM regressor | 14.02% | 33.51% |
| Contextual TCN | 0.80% | 28.83% |
| Contextual flattened-window tree control | -2.31% | 28.83% |

The profit CatBoost classifier won this extension's earlier validation at 17.70%.
It stayed the frozen extension research choice when its later performance failed;
it was not replaced with the later leaderboard leader. The earlier tournament's
TCN remains a separate frozen research control. Neither is approved for trading.

All four calibrated classifier variants failed their historical top-20 readiness
checks. Profit LightGBM's calibrated Brier was 0.2502 versus a 0.2511 constant
baseline, but the improvement was not established and calibration error exceeded
the threshold. Other variants failed improvement/calibration or missing-benchmark
coverage checks. No numeric live confidence is enabled.

The advancement audit additionally requires consistency across validation folds,
positive later returns beating matched momentum under all execution scenarios,
adequate trade count/drawdown, a non-dominating top stock, reasonable quarterly
consistency and an established equal-notional paired-cycle advantage. These are
explicit engineering gates, not a guarantee of profitability. **No arm passed.**
Consequently, the conditional ensemble stage was rejected rather than searching
for a blend of individually unsupported candidates.

Robustness records include the effect of removing the top contributor chosen
from validation alone. For the frozen extension choice that stock was BBOX.NS;
removing it did not rescue the later result. Market beta and residuals are
descriptive attribution, not a claim of risk-adjusted alpha.

## Fresh paper trial

A separate incremental refresh reached **2026-11-06 with 99.8% latest-date stock
coverage**. Twenty-two symbols required full history replacement on a consistent
current provider adjustment basis. The refresh is stored separately from the
warehouse and original training snapshot. Its provider-adjusted prices remain
provisional; historical universe, industry and entitlement limitations remain.

An immutable paired cohort was published on **2026-11-08**, before its first
eligible entry, using a shared pool of **488 stocks**. It records the extension
candidate, the frozen current production model, the earlier TCN control, momentum
and a fractional all-eligible baseline. Current-model weights were copied and
frozen for the trial; inference reproduced the existing model's serving feature
chain without publishing scores or changing production tables.

Each top-20 strategy also has an independent ten-position cohort ledger with
whole shares, cash, risk/industry caps, fees/slippage and gap-aware stops. Open
marks are distinct from matured returns. First observed entry allocations are
stored exclusively; later refreshes cannot resize the initial trades. An entry
price-basis revision requires reconciliation and leaves the book incomplete.
Complex corporate-action wealth is never guessed. Execution remains a paper
assumption based on provider bars, not a claimed exchange fill.

As of the November 6 source snapshot, all four retail books report **pending
entry**, with **zero matured cohorts**. Entry must occur strictly after actual publication, then the holding
window needs 21 benchmark sessions. Cohorts are independent and overlapping
cohort returns are never compounded into a purported portfolio return.

The prospective assessment policy is also frozen before the first outcome
matures. It selects a non-overlapping schedule before checking return availability,
requires at least eight complete independent pairs and 98% matured-pair coverage,
and compares against the three fixed controls with a familywise-adjusted paired
bootstrap interval. Unknown first-cohort outcomes cannot be replaced by convenient
later cohorts. This updates automatically and never promotes a production model.
The first result needs 21 sessions; dependable comparative evidence needs a
longer series of independent observations. Market regimes can still be dependent.

Normal publication updates the paired paper path automatically after the existing
tracking step. Live exports retain enough history for a 200-session market trend
across the temporal windows. A tracking error is visible but does not undo a
successfully published production snapshot. No model is promoted automatically.

## Reproduce and inspect

Use the isolated ML environment described in [model-tournament.md](model-tournament.md).
The verified filing index and original source files are local research artifacts.
To download another timestamped public index export with the poller environment:

```bash
poller/.venv/bin/python ml/evaluation/collect_filings.py \
  --index PATH_TO_TIMESTAMPED_INDEX.json --output NEW_FILINGS_DIRECTORY
```

Run the remaining historical work and audit:

```bash
bash scripts/complete_research.sh
```

This reuses the original frozen snapshot, not refreshed future data. Completed
reports are preserved. A new protocol needs a separate output directory.
The four positional arguments optionally select input, base tournament, output
and filings directories. The default output is
`ml/evaluation/results/remaining_plan_20261108/run/`.

For an isolated refresh and a fresh paired paper update:

```bash
poller/.venv/bin/python ml/evaluation/refresh_research.py \
  --output NEW_REFRESH_DIRECTORY --repair-revisions
bash scripts/completion_paper.sh NEW_REFRESH_DIRECTORY
```

The paper update needs HDFS for the frozen current-model control's legacy
financial inputs. Historical reports and the API do not require running Hadoop.
Source/index/document/model changes are checked and exposed as stale evidence.

`GET /api/research/completion` supplies Forecast Lab with the step status,
calibration, financial coverage, robustness, actual cohort states and explicit
remaining market time. Full protocol, source audits, predictions, simulations,
calibrators, frozen weights and immutable paper records stay in local artifacts.

Tests cover real native-category/classifier APIs, past-only context, publication
timing, ambiguous cumulative contexts, calibration maturity purging, source
guards, open-position accounting, frozen entry fills and price-basis revisions.
The isolated Spark parity fixture also verifies enough context lookback history.
