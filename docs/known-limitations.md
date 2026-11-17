# Known limitations and deliberate non-changes

Things a code review found that were **not** fixed in the hardening pass, and why.
Most share one reason: the file is pinned by a frozen research manifest, or fixing it
would change what the published model does mid-way through a prospective trial.

Check pins before touching code: `python scripts/pinned_sources.py --verify`
(lists 40 pinned files; exits 1 if any no longer matches its recorded hash).

## Pinned code: do not edit, move or reformat

The research modules in `ml/evaluation/` verify each other's sha256 (a protocol records
the hashes of the sources it ran with). Changing one marks the matching dashboard panel
"stale evidence" and makes the frozen paper-trial observers refuse to run
(`paper_observations.verify_frozen`). Also pinned: `spark/jobs/build_training.py`,
`daily_history.py`, `indicators.py`, `ml/feature_engineering/*`,
`poller/universe/universe_cache.json`. A real fix to any of them needs a *new
versioned* experiment/trial, not an edit. `decision_experiment.protected_files()` also
hashes everything under `ml/models/`, so completed reports there stop verifying after a
retrain.

Consequences left in place (all in pinned code): two copies of the XBRL parsing
(`information.document_facts`, `verified_financials.parse_filing`); two copies of the
paper-cohort assessment with different multiplicity divisors; three copies of the
write-once file helper; `audit_published.py` hard-codes its own corporate-action list;
downloaded NSE XML is parsed with stdlib `ElementTree` (prefer `defusedxml` in the next
versioned trial); `try: from .x import ... except ImportError` dual imports.

## The universe cache is refreshed by hand

`poller/universe/universe_cache.json` is hash-pinned by the research manifests, so the
poller no longer re-downloads it when it gets old (it used to, weekly; that marked every
research panel stale and made the frozen paper-trial stages fail with "Feature source
changed"). It is fetched only when missing, or on request:

```bash
python poller/universe/universe.py --refresh   # prints what was added/removed
python scripts/pinned_sources.py --verify      # shows which evidence that invalidated
```

The log warns when the cache is older than about 200 days (NIFTY rebalances in March and
September). Until you refresh, new constituents are not polled and renamed or removed
stocks stay in the list. A refresh stales the research evidence tied to the old file and
stops the frozen paper trial; resume it by freezing a new versioned trial on the new
universe, or retire the trial.

## Train/serve normalisation parity

Training (`build_training.py`), the research exports and the paper control normalise
features across all symbols **including `^NSEI`**; `score_stocks.py` normalises over
stocks only. The effect is one extra row in each date's z-score and winsorisation
quantiles (about 1 of 501), so it is probably small, but the README's "parity by
construction" is not strictly true. Not changed: `build_training.py` is pinned, and
changing the scorer would alter the published rankings in the middle of the prospective
Track Record. Fix both together at the next retrain-and-refreeze.

## Universe benchmark labels are all-or-null

`universe_ret_Nd` is null on a date if any symbol lacks a forward return, so a single
masked symbol (a corporate action) removes the excess-return label for the whole
cross-section on that date. This is intentional and tested
(`test_training_quality_integration`). With the two ledger events it costs about 2%
(5-day) and 4.6% (21-day) of signal dates, and each new restructuring adds roughly 21
trading days at the 21-day horizon. Pinned for the same reason.

## Model selection

* The first-stage choice uses long/short net Sharpe while the product is a long-only
  top-20 list. The final choice is walk-forward IC t-stat; that same t-stat is then
  reported, so it carries selection bias. Hyperparameters are tuned on a validation
  slice that overlaps the earliest walk-forward folds (mild look-ahead). Changing the
  selection rule changes which model is published and cannot be validated without the
  Spark stack, so it was left alone. The retrain now has a floor (positive IC) and the
  deflated-Sharpe trial count includes the ~15 research configurations.
* Multiple testing is disclosed per experiment but never aggregated across the many
  experiments that reuse the same history.

## Evidence lives only on this disk

`ml/evaluation/results/` (~1.5 GB) and `ml/models/` are gitignored. Run
`scripts/backup_results.sh <dir>` regularly. The tracked
`config/pilot_reconciliation_reviews_v1.json` cites PDFs inside that directory and
cannot be verified from a clean clone.

## Infrastructure

* Single-broker Kafka, single DataNode (no fault tolerance); SeaTunnel jobs hard-code
  `localhost:9092` / `hdfs://localhost:9000` (works because everything shares a host).
* `market.deadletter` is written by every job but nothing consumes or alerts on it.
* `bronze.market_quotes` is append-only with no dedup or pruning.
* Docker images are pulled as `:latest`; Airflow defaults to `admin`/`admin`; the API has
  no authentication (ports now bind to loopback by default).
* Several research collectors scrape NSE's private JSON endpoints with
  `impersonate='chrome'`; check NSE's terms before running them regularly.
* `close` is stored as `DECIMAL(18,2)`, which rounds heavily adjusted low-priced stocks.
* The frontend has no unit-test runner; its fixes are covered by `tsc` and the build.
