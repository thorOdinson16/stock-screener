# On-Demand Stock Screening & Scoring Platform

Distributed platform that screens the NIFTY 500 universe: `yfinance` poller -> Kafka ->
SeaTunnel -> HDFS/Iceberg -> Spark (technical indicators) -> Spark MLlib (scoring model) ->
Druid (low-latency ranked queries), driven by a React dashboard and orchestrated by Airflow.

The pipeline is **on-demand** — press **Run pipeline** in the dashboard (or trigger the Airflow
DAG) and it refreshes the whole universe end to end. Nothing runs continuously: the Druid Kafka
supervisors stay suspended at rest and are resumed only for the duration of a run, then suspended
again; the dashboard refreshes only when you click **Refresh data**.

Full spec: see `docs/project-spec.md`.

## Pipeline

```text
React dashboard ──POST /api/pipeline/run──▶ FastAPI ──REST──▶ Airflow DAG
                                                                  │
  yfinance poller ─▶ Kafka ─▶ SeaTunnel ─▶ Iceberg bronze ─────────┤
                                                                  ▼
                        Spark indicators ─▶ silver ─▶ Spark MLlib scoring ─▶ gold
                                                                  │
                    Kafka (market.screener / market.history / market.scores)
                                                                  ▼
                                                          Druid ─▶ dashboard
```

Stages (each is a `scripts/` wrapper run as an Airflow task):

1. **preflight** – check HDFS/Kafka/Druid/SeaTunnel are up and a model is selected
2. **druid_resume** – resume the suspended Druid Kafka supervisors
3. **poll** – fetch quotes and missing daily bars since each symbol’s latest ingested date (fundamentals
   and annual statements too on Full runs) via `yfinance`
4. **ingest** – SeaTunnel drains Kafka into Iceberg bronze
5. **indicators** – Spark computes SMA/EMA/RSI/MACD/volatility/momentum into silver
6. **score** – Spark MLlib scores and ranks the latest cross-section into gold
7. **serve** – publish the latest snapshot to Kafka (history published incrementally)
8. **wait_for_druid** – block until Druid is serving the fresh snapshot
9. **druid_wait / druid_suspend** – wait for lag to drain, then suspend the supervisors
10. **collect_metrics** – snapshot operations metrics

## Stack (native install, no Docker for core services)

| Component  | Version                              |
|------------|---------------------------------------|
| Java       | 17 (`JAVA_HOME`, Kafka) / 11 (Spark, Hadoop, Hive, SeaTunnel, Druid) |
| Kafka      | 3.9.2 (KRaft, no ZooKeeper; Scala 2.12 build) |
| Spark      | 3.3.4 (Scala 2.12.15), PySpark on Python 3.10 |
| Hadoop     | 3.3.6                                  |
| Hive       | 3.1.3 (Metastore only)                 |
| SeaTunnel  | 2.3.13 (Zeta engine)                   |
| Iceberg    | 1.8.1 (`iceberg-spark-runtime-3.3_2.12`) |
| Druid      | 31.0.1 (single-server-small, native)   |
| Airflow    | 3.3.0                                  |
| Maven      | 3.8.7                                  |
| Data source | `yfinance` (Yahoo Finance, unofficial, delayed) |

## Starting the stack

```bash
./start-stack.sh   # HDFS -> Hive Metastore -> Kafka -> SeaTunnel -> Druid -> Airflow
./stop-stack.sh     # reverse order teardown
```

`start-stack.sh` also points Airflow's `dags_folder` at this repo and creates the `screening`
pool. Logs: `~/stack-logs/<service>.log`.

- HDFS NameNode UI - http://localhost:9870
- Hive Metastore - thrift://localhost:9083
- Kafka broker - localhost:9092
- SeaTunnel Zeta REST - http://localhost:5801
- Druid console - http://localhost:8888
- Airflow UI - http://localhost:8080

### Docker (Windows / macOS / Linux)

The same stack also runs under Docker Compose, without a native install, using prebuilt
images from GitHub Container Registry. On Windows, use Docker Desktop with the WSL2 backend
and give it about 14 GB of memory.

```bash
git clone https://github.com/thorOdinson16/stock-screener.git
cd stock-screener
docker compose pull                                     # ~3 GB download, no build
docker compose up -d
docker compose logs -f init                             # wait for "Init complete"
docker compose exec airflow docker/bootstrap-data.sh    # one-time 5y data load + training
```

Then open http://localhost:5173 and press **Run pipeline** (Full, with price history).
Requirements, building from source, settings and troubleshooting: `docker/README.md`.

## Running the pipeline

From the dashboard (recommended): **Run pipeline** (Quick = quotes only, or Full = quotes +
fundamentals; optional universe limit and price-history publish) and **Retrain model**. The
UI streams step-by-step status and refreshes the data when the run succeeds.

Or without the UI:

```bash
scripts/run_once.sh --full --history     # or: scripts/run_once.sh --limit 20
```

Or trigger the DAGs directly in the Airflow UI (`screening_on_demand`, `screening_retrain`).
Overlapping runs are prevented by a 1-slot `screening` pool.

## Web dashboard

```bash
./start-ui.sh   # FastAPI :8000 + Vite dashboard :5173
./stop-ui.sh
```

Pages: **Dashboard** (breadth, gainers/losers, sector performance), **Top Picks** (5d/21d),
**Screener** (filter by industry/score/P-E/RSI/momentum; CSV export), **Stock Detail**
(price + SMA overlays, RSI, MACD, fundamentals), **Model** (IC + Newey–West t-stat,
sector-neutral IC, net-of-cost Sharpe vs the rule baseline), **Ops** (Kafka lag, Druid
segments/latency, HDFS health, pipeline runs), and **Track Record** (prospective
top-20 outcomes, costs, benchmark comparisons and monthly consistency), plus
**Trade Planner** (capital, whole-share sizing, exposure and risk limits). Data updates only when you run the
pipeline and then click **Refresh data** in the sidebar — no background polling.

See `setup.md` for the full ingestion + ML runbook.

## Configuration

- `config/pipeline.env` — service homes, Spark packages/memory, Druid/Kafka endpoints, defaults
- `config/airflow.env` — Airflow REST credentials; copy from `config/airflow.env.example`
  (password in `~/airflow/simple_auth_manager_passwords.json.generated`); gitignored

## Repository layout

```
poller/           yfinance poller + historical daily-bar backfill
kafka/            Topic definitions + create-topics.sh
seatunnel/        SeaTunnel job configs (Kafka -> Iceberg bronze)
spark/jobs/       Spark batch jobs (indicators, training set, scoring, serving publish)
iceberg/schemas/  Bronze / silver / gold + ML table DDL
ml/               Feature engineering, MLlib training/evaluation, saved models
api/              FastAPI backend (Druid queries + Airflow pipeline trigger)
frontend/         React + Vite dashboard (9 pages)
airflow/dags/     On-demand + retrain DAGs
druid/ingestion/  Druid Kafka supervisor specs + on-demand suspend/resume control
scripts/          Pipeline step wrappers (poll/ingest/indicators/score/serve/retrain)
config/           Pipeline + Airflow connection config
tests/            Unit tests (indicators, features, metrics, benchmarks, monitoring)
benchmarks/       Throughput/latency/model/recovery/Iceberg/scalability experiments
monitoring/       Operational metrics + data-quality snapshots
docs/             Project specification and design notes
```

## Notes / limitations

- Data comes from `yfinance` (unofficial, delayed) — fine for research, not for trading decisions.
- Yahoo rate-limits heavy use. If a run's poll step logs "no price data found" or timeouts for
  most stocks, stop it and retry later (usually within an hour). A Full run makes ~3–4 requests
  per stock; a Quick run about half that.
- Daily bars keep the split adjustment they were fetched with. After a stock splits, re-run the
  5-year backfill (`setup.md` §1.6).
- On the 5y training set the signal is modest but holds up across time: walk-forward IC ≈ 0.02
  (5d) / 0.04 (21d), t ≈ 3.6, with technical features plus point-in-time fundamentals from
  annual statements. See `docs/project-status.md` §3 and `docs/model-research.md`.
- Single-broker Kafka (`replication.factor=1`) and a single HDFS DataNode: no fault tolerance yet.
- `market.quotes` Kafka retention is 1 day; run the poller to repopulate it before its Druid datasource exists.

## Incremental daily ingestion

Each pipeline run reads per-symbol progress from `bronze.quotes_daily` in Iceberg,
then downloads only the missing interval plus a 5-calendar-day overlap to finalize
partial bars and capture recent corrections. New symbols bootstrap 5 years by
default. Failed fetches do not advance their stored progress. Druid availability
and scoring dates do not determine download progress.

After SeaTunnel ingestion, an atomic Iceberg overwrite keeps the newest bar per
`(symbol, trade_date)`, including cleanup of duplicates from previous runs. This
currently reads the daily bronze table; Spark indicator computation also still
processes full history. Older trading dates are retained for training/backtests.
Iceberg maintenance expires old snapshots rather than deleting historical bars.

Configure `DAILY_BARS_LOOKBACK_DAYS` and `DAILY_BARS_BOOTSTRAP_PERIOD` in
`config/pipeline.env`. Explicit `backfill.py --start ...` or `--period ...` remains
available for repairs, including refreshing old split adjustments.

## Forward track record

The dashboard's **Track Record** page evaluates lists saved from successful
pipeline publications. It starts prospectively with the next run; old backtests
are not presented as live predictions. Each saved list keeps its original picks,
scores, model metadata, publication universe, and a 10-bps-per-side cost assumption.

Paper entry is the first completed session close strictly after the publication's
India date, followed by a 5- or 21-session hold. Results update on subsequent
pipeline runs; today's possibly partial bars are excluded. Missing prices stay
visible as incomplete cohorts. Completed outcomes are retained across retraining.

The page reports long-only net returns, gross universe/NIFTY 50 benchmarks, pick
hit rate, worst holding-period results and monthly consistency. Overlapping lists
are not compounded; a non-overlapping subset is also counted. Method and caveats:
[docs/forward-tracking.md](docs/forward-tracking.md).

## Retail planning and data quality

**Trade Planner** turns served 5/21-session rankings into paper allocations with
whole shares, existing holdings, cash, stock/industry caps, liquidity/volatility
checks, estimated costs and stop references. It can return no suitable setup.
Scores remain uncalibrated rankings, with no invented probability or target price.

Adjusted-price revisions trigger a full refresh of the affected symbol. Known
unresolved demergers reset indicators and withhold affected returns/training
labels; resulting share values are never guessed. Model provenance identifies
whether retraining used this policy. Temporary 21-day, sector-relative and
63-day experiments appear on the Model page without replacing production models.

Activation, methodology, remaining data requirements and tests:
[docs/retail-research.md](docs/retail-research.md).

**Forecast Lab** assesses profit probabilities and net-return bands against later
historical outcomes. Both tested probability models failed the baseline checks;
live confidence figures remain withheld. The page exposes the evidence and
corporate-action valuation gaps. Reproduction and results:
[docs/forecast-calibration.md](docs/forecast-calibration.md).

Forecast Lab also compares the 21-session walk-forward model with simple momentum
and an all-eligible equal-target portfolio under common execution rules. A separate
equal-notional diagnostic distinguishes stock selection from cash allocation.
[Protocol and measured results](docs/baseline-comparison.md).

The **architecture tournament** compares ridge, LightGBM, CatBoost and a small
temporal convolutional model with matched controls, execution-aligned targets,
cost/delay stress tests, and the actual ten-position planner. Experimental paper
tracking stays separate from production. [Method and commands](docs/model-tournament.md).

The remaining plan stages have also been run: verified NSE financial subset,
market/sector context, categorical inputs, separate profit/outperformance models,
calibration and robustness checks. Fresh paired paper signals are recorded; their
21-session outcomes are pending. [Completion evidence and limits](docs/research-plan-completion.md).

Six fixed mathematical ranking equations have also been tested against matched
momentum controls. None established a stronger later trading result.
[Equations, reproducible experiment and results](docs/quant-equations.md).
