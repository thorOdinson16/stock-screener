# Prospective top-pick tracking

The Track Record page (`/performance`) evaluates the lists actually published by
the deployed system. This is a long-only paper experiment, separate from the
historical long/short evaluation on the Model page.

## Recording and persistence

`publish_screener.py` gives every snapshot one fixed timestamp and writes a
publication manifest with its expected row count and selected model metadata.
`wait_druid.sh` waits for that exact timestamp and row count. It then runs
`track_forward.py`, which reads the served ranks and captures each horizon's top
20 (or fewer if fewer are available).

The record contains the publication timestamp, scores/ranks, model metadata and
fingerprint, complete publication universe, and cost assumption. Prediction files
are immutable and keyed by snapshot timestamp, so a retry cannot replace picks.
Captures older than ten minutes are rejected: old historical picks cannot be
silently reconstructed as prospective evidence.

Files live in `ml/evaluation/results/forward/`: `predictions/*.json`,
`expected.json`, and `report.json`. This directory uses the existing shared state
volume in Docker and survives restarts/retraining. Back it up with model results.
No changes to Druid datasources or Iceberg schemas are required.

## Entry, exit and costs

- Convert publication time to Asia/Kolkata's date.
- Enter at the close of the first benchmark session strictly after that date.
  This conservative proxy avoids buying at a price that predates the prediction.
- Exit at the close 5 or 21 benchmark sessions after entry. Every stock uses the
  same entry/exit dates; missing bars never shift an individual stock's exit.
- Sessions come from stored NIFTY 50 (`^NSEI`) daily bars. Today's bars are excluded
  even after market close, to avoid treating an intraday partial bar as final.
- Hold equal initial allocations across the saved picks. Each independent cohort
  pays 10 basis points on entry and exit. For gross return `r` and cost `c = .001`,
  net return is `(1 + r) * (1 - c) / (1 + c) - 1`.

This intentionally differs from the training target's score-date close entry.
It tests whether a published ranking remains useful after it becomes available.
The close is a paper execution assumption, not a claim that an order was filled.

## Benchmarks and missing data

The universe benchmark holds every symbol in the saved publication universe at
equal weight. The second benchmark is NIFTY 50. Both are gross of trading costs;
pick portfolio returns are net of the stated costs, making the comparison explicit.

A pick hits when its gross return strictly exceeds the universe's gross return.
The portfolio wins when its net return exceeds that benchmark. These are different
questions; the UI labels both. A tiny numerical tolerance avoids counting a
floating-point tie as a hit.

A cohort is pending until its holding window ends. If any pick or benchmark
constituent lacks a valid entry or exit price, it is incomplete. Known individual
outcomes remain inspectable, but no complete portfolio performance is reported and
no missing stocks are silently removed. Future runs retry incomplete cohorts.
Completed outcomes, including entry/exit prices for the picks, are retained as
reported rather than rewritten when models or stored prices change.

## Reading aggregate results

Summary cards and monthly charts average completed cohort returns. Entry month
determines the monthly group. Worst cohort and worst pick are holding-period
returns, not intraperiod maximum drawdown. Empty samples show no return estimates.

Frequent lists overlap in holdings and holding periods. Their returns are neither
independent observations nor a continuously funded portfolio, so they are never
compounded or annualized. A chronological subset takes the first complete cohort
and then the next one entering on or after the previous exit. Its count and mean
excess return provide an additional view; it is not a significance test.

Inspect cohort model fingerprints when comparing periods: retraining can change
the deployed model, and combined results do not validate one unchanged model.

## Operational behavior and limits

Results update during `wait_for_druid` on a normal successful run, in both Airflow
and `scripts/run_once.sh`. There is no background schedule. Missing the next few
pipeline runs delays evaluation, but stored daily bars allow it to catch up.
`GET /api/model/forward` reads the latest report and returns a valid empty report
before the first run. Tracking failures propagate as pipeline task failures.

Yahoo's adjusted daily prices remain an approximation. Restated history,
corporate actions, missing benchmark sessions, delistings, and real execution
costs can affect the result. Incomplete cohorts must be examined alongside the
completed sample; a high completion-filtered hit rate alone is not sufficient
evidence. Longer prospective history across market conditions is still needed.

Validation: `python3 -m unittest discover -s tests -p test_forward_tracking.py`.
The opt-in `tests/test_forward_integration.py --spark` uses a temporary Iceberg
warehouse and fixture Druid responses; it does not require the live stack.

## Historical replay

`ml/evaluation/backtest_published.py` replays this entry/exit and cost methodology
against five expanding historical folds. Each fold refits the production rank
ensemble specifications using only earlier observations, with a 21-session embargo
and an additional check that training labels end before the test period. It writes
its own prediction checkpoints, cohort/pick CSVs, JSON report, and Markdown summary.
It does not populate the prospective page or change production model selection.

With HDFS running, use the existing project Spark wrapper:

```bash
source scripts/lib.sh
spark_submit "$ICEBERG_PKG" "$REPO_ROOT/ml/evaluation/backtest_published.py" \
  --warehouse "$WAREHOUSE" --output "$REPO_ROOT/ml/evaluation/results/published_backtest"
```

The model specifications and features were already chosen through research on
this history, so earlier-only fitting does not make the replay an untouched test.
The report retains that limitation and the missing-data counts. Non-overlapping
portfolio windows are scheduled before checking outcomes; any missing scheduled
window prevents reporting a cumulative return rather than silently skipping it.
