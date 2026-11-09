# Fixed financial pilot and data readiness gate

This milestone pauses model experiments and builds an auditable financial dataset.
It does not train, tune, replace or promote a model. Existing paper-trial weights,
publication records, feature snapshots and trading rules stay frozen.

## Pilot selection frozen before collecting reports

[config/financial_pilot_v1.json](../config/financial_pilot_v1.json) contains the
20 fixed securities, their ISINs, industry labels and selection scores. Selection
uses median close times volume over the 63 completed benchmark sessions ending
2023-06-30, with at most three stocks per industry. Financial Services and Unknown
are excluded so this first pilot uses one comparable operating-company taxonomy.
No forward returns or model outcomes enter selection. The initializer refuses to
reselect an existing pilot; difficult stocks are not replaced after inspecting gaps.

The frozen stocks are RELIANCE, ADANIENT, INFY, TMPV, TCS, LT, ETERNAL, MARUTI,
ADANIPORTS, ITC, BHARTIARTL, HAL, HINDUNILVR, TATASTEEL, M&M, ASIANPAINT,
SUNPHARMA, TITAN, HINDALCO and DLF. These are data-research constituents, not trade
recommendations. Current universe membership and industry labels, and adjusted
historical liquidity prices, introduce survivorship/hindsight limitations despite
the lagged liquidity calculation. The pilot is not a point-in-time index universe.

## Coverage gap found and repaired at the source

NSE exposes older records through its [financial-results page](https://www.nseindia.com/companies-listing/corporate-filings-financial-results)
and newer records through [Integrated Filing](https://www.nseindia.com/companies-listing/corporate-integrated-filing).
The latter page's [official client code](https://www.nseindia.com/dist/js/sections/corporate-filings.js)
uses the paginated `/api/integrated-filing-results` endpoint, including the exact
`Integrated Filing- Financials` type. Relying on the legacy endpoint and recent
RSS alone caused much of the preceding experiment's coverage gap.

The collector queries both Quarterly and Annual legacy indexes and every page of
the integrated index for each frozen symbol. Annual index entries are not assumed
to be quarterly facts: the original XML must still contain an explicit valid
75–105-day consolidated quarter. It never derives an unverified fourth quarter
by subtracting unrelated annual and nine-month reports.

Pagination checks totals, empty early pages and duplicate records. Request/schema
failures are distinguished from rejected index rows, so a bad duplicate filing
cannot masquerade as a transport failure or an accepted document. Both remain
visible. Original and revision documents are retained as separate versions.
Repeated quarterly/annual indexes of the same bytes use the latest evidenced
publication time, never the earliest convenient timestamp. Raw index responses,
document bytes, hashes and immutable observation receipts are retained.

Official legacy index records can bind an old as-filed symbol to the pilot's exact
ISIN. Only that explicit binding permits canonicalization of a rename such as
ZOMATO to ETERNAL. The as-filed symbol, ISIN and mapping basis remain in the audit.
Without that binding a symbol mismatch remains quarantined. A verified security
identity does not establish economic comparability across a demerger; corporate
action scope/execution limitations still apply.

## Acceptance and temporal rules

Every accepted fact requires original issuer identity, consolidated scope, INR
units, a whole-company quarter, a consistent reporting end and explicit exchange
publication timestamps. Segment/YTD totals cannot stand in for quarterly revenue.
Missing, malformed or ambiguous contexts remain rejected; no synthetic contexts
are inserted just to improve coverage.

Financial features become available on the calendar day after verified
publication. Quarterly and yearly comparisons must already have been public on
the signal date. Stale current reports expire after 180 days from period end.
Later amendments do not rewrite earlier features or replace a newer fiscal
quarter with an older one. Unknown margins or growth values stay null, never zero.

The dataset includes source-document hashes for each active filing and its prior
quarter/year comparisons. `daily_coverage.csv` retains every benchmark signal
date; no missing-data dates or stocks are discarded to improve the result.
Historical reconstruction using archived original publication evidence is not a
claim that our system observed those filings contemporaneously. Prospective
collection retains actual first observation separately.

## Hard readiness gates

These thresholds were frozen with the pilot, before the source audit:

- At least 504 signal dates.
- On a passing signal date: margins on at least 95% of stocks, quarterly changes
  on at least 95%, and yearly revenue growth on at least 90%.
- At least 95% of all signal dates pass, with no calendar signal quarter below 90%.
- The final signal date must pass.
- At least 95% of the 360 required stock-quarter cells, 2022Q1 through 2026Q2,
  have an accepted original document.
- Source requests and pagination must complete. Rejected records remain excluded.

`require_ready()` and `--check-ready` raise before any prospective verified-pilot
training can proceed. Changed pilot definitions, dataset artifacts, collection
receipts or feature definitions require another audit. A passing data gate would
only permit a separate controlled experiment; it would not establish model skill.
No new model was trained in this milestone. This gate applies to the verified
financial pilot; the existing main pipeline and retrain workflow are unchanged.

## Actual audit result after original-report reconciliation

**Status: ready for controlled research.** The original XML audit remains intact:
337 accepted XML filings and 53 rejected XML filings. A separately reviewed set of
41 original quarterly PDF statements resolves all required gaps, giving 378
accepted filing records. Invalid XML contexts were not repaired or accepted.

| Measure | Before reconciliation | After reconciliation |
|---|---:|---:|
| Fixed stocks | 20 | 20 |
| Signal dates, 2024-01-01 through 2026-10-26 | 695 | 695 |
| Signal dates passing the fixed rules | 68.8% | 100% |
| Required stock-quarter cells | 319 / 360 | 360 / 360 |
| Missing required quarters | 41 | 0 |
| Latest margins / quarterly changes / yearly changes | 20 / 20 / 19 | 20 / 20 / 20 |

Every calendar signal quarter passes. The pilot list, dates, feature requirements
and thresholds are unchanged. This establishes financial-data coverage, not
predictive skill, and no model was trained or promoted.

[The versioned review ledger](../config/pilot_reconciliation_reviews_v1.json)
records each original URL, PDF page, consolidated quarter, currency, printed unit,
revenue/profit labels, amounts, dissemination receipt and source hashes. The
loader validates original bytes and timing before joining facts. Original
announcement indexes, PDFs and extracted text are under `reconciliation/` in the
runtime dataset. The pre-reconciliation audit is retained in
`before_reconciliation/`. Source snapshots remain ignored runtime data; the review
ledger alone cannot recreate missing original bytes. Restoring this dataset on a
new machine requires these archived snapshots; changed source bytes need review.

Several historical announcement/financial index ISINs differ from the frozen
pilot ISINs. The [official NSE security master](https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv)
binds the exact same legal issuer name and exchange symbol to the frozen ISIN.
The original PDF also names that issuer. Both identities and the master hash are
retained. This establishes issuer-level financial identity, without asserting
price or entitlement comparability across corporate actions.

The review preserves INR million versus lakh versus crore units; total group
profit versus owner-only profit; and explicitly printed quarterly figures.
For example, Sun Pharma's original statements distinguish profit before and
after non-controlling interests. Titan's March 2022 PDF reports group profit of
527 crore while a tentative malformed XML shows 526.463 crore: the independent
PDF figure is used, and the discrepancy is recorded. HUL's revenue components
are explicitly summed and the PDF's rounded profit is retained. No quarter is
backfilled by subtracting annual and nine-month results or by using a future
comparative restatement. Day-only issuer publication uses conservative end of day
plus the standard next-calendar-day availability rule.

Changing a reviewed source, ledger, identity evidence or coverage definition
invalidates readiness until another audit. The dashboard checks those hashes
without importing ML packages.

## Run

```bash
bash scripts/financial_pilot.sh                 # collect or resume existing checkpoint, then audit
bash scripts/financial_pilot.sh --resume        # cached indexes/docs, retry incomplete work
bash scripts/financial_pilot.sh --refresh       # fetch fresh indexes/docs; preserve old observations
ml/.venv/bin/python ml/evaluation/pilot_coverage.py --check-ready
```

The readiness check now passes for the archived reconciled dataset. The audit
command can also succeed while reporting an unready dataset; callers must use
`--check-ready` before a new verified-pilot experiment. Raw collection
uses the existing poller environment; analysis uses `ml/.venv`. No HDFS service,
cron job or external deployment is created.

Artifacts in `ml/evaluation/results/financial_pilot_v1/` include original raw
responses/documents, observation versions, `collection.json`,
`pilot_features.parquet`, `daily_coverage.csv`, `coverage_report.json`,
`coverage_summary.md` and `review_queue.json`. Large runtime data stays ignored
by Git; the versioned pilot definition, collectors, tests and documentation are
kept as repository source. Forecast Lab displays readiness and per-stock coverage
through `GET /api/research/financial-pilot` without loading ML libraries in the API.

Existing frozen paper cohorts can now be valued independently of new scoring:

```bash
bash scripts/paper_outcomes.sh
```

The regular tournament-paper pipeline invokes that observer before reference
scoring. It fetches completed observations, preserves first observed prices,
quarantines revisions during the holding window, and checks frozen protocol,
execution-source and model hashes. No fitting or prediction capture occurs in
this command. Run after 18:00 IST for the current completed session; future
sessions are collected when the on-demand command/pipeline runs. No background
scheduler was installed.

Latest actual observation: 9 November 2026, 74 symbols requested, one provider
quote error (EMBASSY.NS), one captured cohort and zero matured outcomes. The
cohort was published on 9 November and awaits its first later benchmark session.
The quote error remains visible; it is not filled with an invented price.
All prior paper predictions, feature snapshots and model files retain their
original hashes.

The subsequent [fixed financial-feature comparison](verified-pilot-experiment.md)
has now run using the reconciled dataset. No candidate qualified; this does not
change the audit result or the existing frozen paper trial.
