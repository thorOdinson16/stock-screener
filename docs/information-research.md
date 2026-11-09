# Timestamped information and prospective research

The new pipeline collects original NSE filings and announcements separately from
production, computes publication-safe financial changes and richer peer context,
runs a fixed information ablation, and records immutable prospective paper
cohorts. It does not change production rankings or claim an established edge.

## Sources and audit

Sources are the official [NSE announcements page](https://www.nseindia.com/companies-listing/corporate-filings-announcements),
[financial-results index](https://www.nseindia.com/companies-listing/corporate-filings-financial-results)
and [integrated financial RSS feed](https://nsearchives.nseindia.com/content/RSS/Integrated_Filing_Financials.xml).
The [NSE RSS directory](https://www.nseindia.com/static/rss-feed) lists the public
feeds. Each collection keeps raw response bytes, source URLs, hashes, exchange
publication times and actual first observation times. No provider-generated voice
summary or inferred sentiment is treated as an original financial fact.

The initial bounded collection archived 851 filing versions and 1,754 announcement
records. The parser accepted 674 distinct filing versions across 382 symbols and
rejected 177 records. Rejections include ambiguous/non-consolidated quarterly
contexts, inconsistent security identities, missing timestamps and inconsistent
reporting periods. The count of archived records is not a completeness claim.

Historical financial collection supplements the existing corpus with original
quarterly reports for the first 60 alphabetically ordered securities in the
current universe, subject to a 420-download budget, plus recent integrated RSS
filings. Alphabetical selection is fixed independently of trading outcomes;
coverage is partial and uneven. Current security names that disagree with an
original filing are quarantined rather than silently mapped through a rename.

## Information features

**Financials:** consolidated quarterly INR revenue and profit, profit margin,
quarterly revenue growth, profit change scaled by previous-quarter revenue,
margin change, yearly revenue growth and filing age. Financial contexts must be
whole-company quarters of 75–105 days, never segment or cumulative YTD totals.
Quarterly/yearly changes require matching original prior periods and consistent
scope/currency. Missing comparisons stay unknown; these are reported growth
figures, not earnings surprises against analyst consensus.

A filing enters daily historical features only on the calendar day after its
verified exchange publication. The latest reporting period takes precedence over
a later amendment of an older quarter. Features expire 180 days after period end.
Only versions public at that feature date may affect a comparison. If changed
bytes are observed at the same URL/publication timestamp, that revision is delayed
until the day after actual observation. Raw cached documents remain immutable;
silent changes to a previously cached URL are not continuously re-downloaded.

**Sector context:** leave-one-out breadth above the 200-day average, breadth of
positive 21-session returns, peer mean return, 63-session return dispersion,
stock-minus-peer return and peer volume expansion. Require five other peers.
Prices use common-calendar windows without filling gaps. Missing context remains
unknown. Current universe and industry membership remain historical limitations.

**Announcements:** a separate versioned observation ledger stores original
exchange subjects, dissemination times and attachment links. The feature helper
requires both publication and actual observation, deduplicates versions, and
returns unknown counts without verified observation coverage. Announcement counts
and categories are excluded from predictive models for now: a retrospective
seven-day response is not seven days of continuous collection, and no matured
announcement-training history has yet been established. Absence is not zero news.

## Controlled historical comparison

All three models use the same technical inputs, eligible inference rows, target,
chronological splits, training maturity purge, execution rules and costs. The
only model is a fixed LightGBM regressor: seven leaves, 100 trees, learning rate
0.04, minimum child size 100, L2 10 and seed 42. Missing-value preprocessing fits
training only, with explicit missingness indicators. No hyperparameter search.

The arms are technical control, technical plus six richer sector features, and
those features plus six verified earnings features. The target is fixed-policy
21-session net return. Validation is 2024-04-05 through 2025-10-14, assessment
2025-10-15 through 2026-10-26. These periods have already been researched.

The earnings arm passed the predeclared validation gate: positive independent
basket selection, stable fold advantage, and positive actual retail outperformance
under base, doubled-cost and delayed-entry scenarios. Sector-only failed its
positive doubled-cost requirement. The validation choice was frozen before later
assessment; it is not a production promotion.

| Inputs | Validation retail net | Later retail net | Later doubled costs | Later extra entry delay |
|---|---:|---:|---:|---:|
| Technical control | -3.01% | +20.97% | +20.25% | Incomplete |
| Richer sector context | +4.08% | -15.51% | -11.99% | -12.02% |
| Sector plus verified earnings | +8.15% | -1.01% | -6.01% | -5.75% |

Matched momentum on this experiment's inference pool returned **+33.51%** later.
This pool differs from the earlier Kalman/XGBoost common pool; do not compare
its benchmark directly with that experiment's +34.70%. Returns are cumulative
net portfolio returns, not annual rates. Higher-cost runs can change quantities
and holdings; their differences are not pure fee attributions.

No input extension establishes a stronger predictor. In particular, financial
coverage deteriorates sharply outside the legacy archive period: the first
assessment date has zero eligible financial features, and the final date has
only two stocks with usable margins and quarterly changes. Thus the later
financial arm mostly exercises its missing-data behavior; it is not a broad,
fresh financial-information comparison. This limits conclusions about the value
of earnings information itself. The technical control's delayed-entry and
independent-basket diagnostics also contain unresolved valuation gaps.

Historical feature coverage includes quarterly changes for 38 symbols and yearly
revenue changes for 31, versus margins for 365. Rich sector context covers 480
symbols where enough contemporaneous peers exist. All counts describe this
partial corpus, not every stock on every date.

## Frozen prospective trial

Three separately frozen models record the prespecified information ablations,
including the technical control, regardless of historical selection. They are
research controls rather than endorsed trading models. The comparison also
records the frozen current-model reference and matched momentum on one shared
eligible pool. Existing paper trials and production models remain untouched.

The first cohort was actually recorded on **2026-11-09**, using completed price
history through **2026-11-06** and information observed before the actual capture.
There are **489 stocks**, one cohort and **zero matured outcomes**. Information
observed on November 9 is never backdated into a November 6 forecast. Entry is
at the first completed market-session close strictly after actual publication.

Live feature coverage at initial capture: four usable filing margins, two
quarterly revenue/profit/margin changes, one yearly revenue change, and 479 stocks
with richer sector context. The other financial inputs remain missing. Full
feature snapshots, actual information cutoff, price session, document hashes,
model hashes and matching current-model score receipt are recorded per cohort.

Prediction files are exclusive and immutable. Retries do not rewrite a cohort
or its captured feature file. Each strategy gets an independent ten-position
paper ledger. The first observed entry quantities, stops and cash are frozen;
price-basis revisions or unvalued corporate actions produce incomplete outcomes.
Overlapping cohorts are not compounded into a purported account return.

Six comparisons are fixed: each information extension versus technical control,
current model and momentum. The greedy chronological nonoverlap schedule is
selected before checking outcomes. At least eight completed independent cycles,
98% matured-pair coverage and positive familywise-adjusted bootstrap intervals
are required for descriptive support. Five thousand draws and seed 42 are fixed.
The trial can only accrue evidence in future sessions. Production readiness
remains false even if a paper support flag eventually passes.

## Run and inspect

Initial collection, historical comparison and frozen trial:

```bash
bash scripts/information_research.sh <fresh-export-directory> <matching-current-model-scores.json>
```

After the trial is frozen, append current information and capture/evaluate without
retraining:

```bash
bash scripts/information_paper.sh <fresh-export-directory> <matching-current-model-scores.json>
```

Collection uses the poller environment; research uses the existing isolated ML
environment. `INFORMATION_ARCHIVE` and `INFORMATION_RESEARCH` select directories.
Initial download bounds can be set with `INFORMATION_HISTORY_SYMBOLS` and
`INFORMATION_MAX_DOWNLOADS`. Later paper collections skip the legacy bulk-history
request and bound current document downloads to 60. Collection failures are
recorded; no successful-coverage claim is inferred from a failed request.

The existing completion-paper stage invokes this optional trial after obtaining
matching fresh price and current-model exports, once its report exists. An
information-stage failure is logged without failing production publication.
The application remains on demand: no new cron job, background collector or
external deployment was created. Use future pipeline/paper runs to continue.

Artifacts live under `ml/evaluation/results/information_archive/` and
`information_research_20261109/`. Forecast Lab has an information-research card;
`GET /api/research/information` serves source-checked historical results and fresh
coverage without importing training libraries into the API. Changed source,
filing, model or historical input evidence marks the card stale.

Twelve focused checks cover publication delay, missing comparisons, stale reports,
late amendments, observation timing, peer causality/self-exclusion, unknown
announcement coverage, strict original document identity/scope/units, immutable
observation records and outcome-independent cohort scheduling. Three API tests and
frontend typecheck/build also pass. Existing model, forecast and paper-ledger
checks remain separate.

## Independent outcome observation

`bash scripts/paper_outcomes.sh` values already frozen cohorts without a fresh
export, reference scoring, retraining or new signals. The regular tournament-paper
stage runs it before scoring. Completed daily quotes are observed after 18:00 IST;
first observations are preserved and subsequent holding-window price revisions
remain quarantined. Failed quotes stay missing. Protocol, model and execution
source hashes must match the frozen trial before valuation proceeds.

As of 9 November 2026: prices observed through that session, one captured cohort,
zero matured cohorts, one quote error for EMBASSY.NS. Entry must occur after the
cohort's9 November publication; the 21-session result is still in the future.
Run the command or regular pipeline on future days to accrue observations; no
background scheduler was added. The reconciled 20-stock financial pilot is a
separate historical dataset and does not rewrite this trial's features or models.
