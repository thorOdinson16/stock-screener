# Architecture and target tournament

The subsequent context, verified-financial, probability/calibration, robustness
and fresh paired-paper stages are documented in
[research-plan-completion.md](research-plan-completion.md). Their paper outcomes
still require future market sessions; no replacement is promoted.

This is a read-only, 21-session historical research pipeline. It compares model
families, feature sets and targets, then freezes the validation choice for a
separate prospective paper trial. It never writes to production model selection,
Iceberg training tables, Kafka scores or served rankings.

## Candidates and inputs

The tabular tournament contains twelve arms: three learners × two feature sets ×
two targets. Each learner receives the same finite feature rows and the same
chronological fold definitions within an arm.

| Learner | Fixed tuning budget | Learning objective |
|---|---|---|
| Standardized ridge regression | Alpha 1 or 100 | Within-date target ranks |
| LightGBM 4.6.0 | 7 leaves/100 trees or 15 leaves/150 trees | LambdaRank, ten integer relevance grades with linear gains |
| CatBoost 1.2.8 | Depth 4 or 6, 150 iterations | QueryRMSE on within-date target ranks |

Feature sets are the shared fourteen technical inputs (`price_only`) and those
inputs plus the six existing fundamental inputs (`legacy_all`). Annual statements
have estimated reporting lags, not verified publication dates or as-reported
versions; the fundamental arms are provisional ablations and cannot qualify for
paper selection. Industry categories are not selectively added to one learner.
Historical industry/universe metadata still introduces a survivorship limit.

The first target is the existing 21-session cross-sectional excess return. The
second uses next-session-close entry, an 8% stop and a 21-session exit after
entry, with fees and slippage each 10 basis points per side. Both are transformed
to within-date ranks for training. LightGBM discretizes those ranks into ten
relevance grades; the others retain continuous ranks. These are learner/loss
comparisons on shared underlying targets, not a loss-controlled causal study.

The sequence experiment adds a fixed small temporal convolutional network (TCN):
twenty sessions of the fourteen technical inputs, sixteen hidden channels,
causal convolutions with dilations 1/2/4/8, and three CPU epochs. Scaling is fitted
on training windows only. A fixed LightGBM control receives the same flattened
windows and exactly the same available sample rows. This distinguishes temporal
architecture from the extra historical input information. There is no transformer
or ensemble search in this bounded experiment.

Cached out-of-sample predictions from the existing Spark tree ensemble provide a
historical reference. Momentum is an additional non-ML control. It uses the close
exactly 63 benchmark sessions before the signal date, without skip-month tuning.

## Chronology and selection

Five expanding folds use the existing research test dates and a 63-session
embargo. Training also requires the target's scheduled maturity to precede the
test block. Early stops do not exempt an observation from that maturity purge.
Missing calendar dates, corporate entitlements and required prices stay unknown.
Training omits unavailable labels; inference is not filtered by future outcomes.

Folds 1–3 select each family's configuration by mean daily Spearman rank
correlation, with the lower grid index breaking ties. Weights give each training
date equal total weight. There is no random train/test split or assessment-driven
early stopping. The sequence architecture/control have fixed configurations.

The paper choice is locked before fitting or evaluating folds 4–5. It must use
price-only inputs and the execution target, have positive rank IC in at least two
validation folds, at least twenty closed trades and drawdown no worse than -25%,
and beat its matched momentum control under base, doubled-cost and delayed-entry
ten-position simulations. Highest base validation return selects among qualifiers.
This is a paper-research gate, not evidence of a reliable trading edge.

Each candidate's momentum control uses its **identical available stocks and signal
dates**, including the sequence-window availability restriction. Raw returns from
different availability pools must not be treated as pure architecture comparisons.
The initial report was corrected to enforce this matching; its superseded output
is archived under `ml/evaluation/results/tournament_history/`. This correction
does not create a new untouched test: all of this history was previously explored.

## Measured outcome: no replacement qualifies for production

The completed run exported 565,289 rows and retained 465,093 rows after common
feature warm-up/finite-input checks. It performed 106 fold fits, followed by one
refit of the validation-selected paper model. Cached predictions provide the
current-ensemble reference; momentum requires no fitting.

The earlier validation period is 2024-04-05 through 2025-10-14. Later historical
assessment is 2025-10-15 through 2026-10-26. Returns below use the actual
ten-position retail allocator after the specified fees and slippage; they are
cumulative period returns, not annualized returns.

| Candidate | Earlier net return | Later net return | Later matched momentum | Later max drawdown |
|---|---:|---:|---:|---:|
| Price-only, existing target, LightGBM | 1.47% | 8.63% | 33.51% | -16.93% |
| Price-only, existing target, CatBoost | Incomplete | 16.82% | 33.51% | -10.55% |
| Price-only, execution target, LightGBM | -1.55% | -9.79% | 33.51% | -10.56% |
| Price-only, execution target, CatBoost | -3.59% | -5.06% | 33.51% | -8.31% |
| TCN, execution target | 21.96% | -2.27% | 28.83% | -8.07% |
| Flattened-window LightGBM, execution target | -13.17% | -2.26% | 28.83% | -8.00% |

The TCN was the validation-only paper choice and stayed frozen when the later
assessment disappointed. It also failed the later doubled-cost and delayed-entry
matched-momentum comparisons. The TCN and its equal-input tree control had 214
later signal dates; ordinary tabular arms had 252. The dashboard therefore shows a
separate matched control for each row rather than using the broad-pool momentum
return to judge the sequence models.

The current-ensemble reference's later portfolio is incomplete at the HEG
restructuring window; it has no asserted full-period return. Incomplete candidate
runs also remain unavailable. Complete new candidate portfolios did not beat their
matched momentum controls in the later period. None is promoted, and momentum is
not promoted merely because it led this already researched sample.

The execution-aligned targets did not produce an improvement in this run. Their
training coverage also differs from the existing target, whose whole-date
universe benchmarks are withheld when a constituent label is missing. That
comparison does not isolate the effect of target definition alone. The small
fixed grids and three-epoch TCN do not establish that every configuration of an
architecture would fail.

Fresh prospective cohorts: **zero** as of 2026-11-08. The source ends on
2026-10-26, and the paper path correctly reports `waiting_for_fresh_data`.

## Portfolio and uncertainty views

The primary simulator is the actual retail planner: ₹100,000 initial capital,
ten positions, whole shares, cash, per-stock/industry/risk caps and participation
limits. It uses known signal ranks, next-close entry, gap-aware stops, and marks
held stocks daily. An unresolved valuation stops a portfolio with an explicit
incomplete result. No return is invented for that run.

Stress scenarios double both fee and slippage assumptions, or delay entry by one
additional benchmark session. Allocation paths can change, so a higher-cost book
need not be a simple fixed deduction from the original return.

The report also retains non-overlapping equal-target baskets and equal-notional
selection diagnostics, with the paired descriptive intervals from the baseline
comparison engine. The latter excludes capital allocation constraints and is not
an executable retail portfolio. Dependent dates, multiple comparisons, adjusted
prices, restated fundamentals and current-universe membership prevent strong
statistical or live-performance claims. Probability calibration remains withheld.

## Prospective paper trial

A validation-qualified choice is refitted on available matured outcomes and saved
outside production, with model/protocol hashes and its training maturity cutoff.
Its later historical assessment is shown explicitly; failure does not trigger a
switch to the later leaderboard winner. A failed choice remains a labelled
research control, never an approved trade recommendation.

Paper snapshots are published using the actual current India date and immutable
daily files. Signals older than five calendar days cannot start a cohort. The
first paper entry is strictly after the actual publication date, even if the
underlying signal date is earlier. Retries cannot rewrite a capture. Candidate,
matched momentum and all-eligible equal-notional cohorts use the same source pool.
They retain missing outcomes and are not compounded across overlapping cohorts.

After normal production publication/forward tracking, `wait_druid.sh` updates this
separate paper path when a frozen candidate exists. The live exporter reads recent
action-aware silver indicators, applies the shared training transforms, and
exports enough raw history for the 63-session control and existing cohorts. It
does not recompute full-history indicators or retrain. A paper-stage failure is
reported without undoing successful production publication. Changed feature
definitions or action evidence require a separately versioned research trial.

## Run and inspect

Create an isolated environment; Spark/API/poller environments are not modified:

```bash
python3 -m venv ml/.venv
ml/.venv/bin/pip install -r ml/requirements.txt
ml/.venv/bin/pip install -r ml/requirements-sequence.txt
bash scripts/tournament.sh
```

The first export requires HDFS. Existing exports allow the CPU tournament to run
without Hadoop. Optional script arguments select input and output directories;
the default research/reference run is `retail_research_20261108`. The Python CLI
also accepts `--research`; omit `--sequence` for a tabular-only run.

Outputs under `ml/evaluation/results/model_tournament_20261108/` include the locked
protocol, outcome audit, per-fold prediction/checksum receipts, validation-only
selection, detailed simulations, report/summary and frozen paper artifact. The
protocol records source/data hashes, library versions, grids, features, objectives
and execution rules. A changed protocol refuses to resume in the same directory.
The shell runner treats completed or already frozen runs as read-only and does
not refit them in place; choose a new output directory for another experiment.

To update paper results manually after a normal pipeline run:

```bash
bash scripts/tournament_paper.sh
```

Or pass an already exported input directory. The initial source ends on
2026-10-26, so it cannot start a fresh cohort on 2026-11-08. No historical cohort
is fabricated to fill that gap.

`GET /api/research/tournament` serves the compact report in Forecast Lab. Source,
reference and manifest changes mark evidence stale. API report reads require no
training-library dependencies. Full curves, trades and per-cycle records remain
in the detailed artifacts.

## Validation and references

Tests exercise real ranking-library APIs, maturity purging, tied labels, causal
windows, matched controls, validation-only selection, immutable captures, actual
publication timing and report guards. An isolated Spark fixture verifies live
technical features against the training transform, including benchmark
normalization, and sufficient raw history for momentum. It uses no live warehouse.

Official implementation references:

- [LightGBM 4.6.0 ranker API](https://lightgbm.readthedocs.io/en/v4.6.0/pythonapi/lightgbm.LGBMRanker.html)
- [CatBoost ranking objectives](https://catboost.ai/en/docs/concepts/loss-functions-ranking)
- [PyTorch 2.6 Conv1d](https://docs.pytorch.org/docs/2.6/generated/torch.nn.Conv1d.html)
