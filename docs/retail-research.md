# Retail planning and research

This iteration adds a paper Trade Planner, corporate-action quality controls,
finite-capital portfolio accounting, and controlled 21/63-session experiments.
It does not claim a verified increase in predictive accuracy or place broker orders.

## Using the dashboard

Open **Trade Planner**. Enter total capital (including holdings), a planned
per-position loss budget, stock/industry exposure caps, a stop distance, liquidity
and volatility limits, and estimated fees/slippage. Existing holdings are optional:
one `symbol, shares, current price, industry` per line. Served industry metadata
takes precedence where available, avoiding accidental bypass of industry caps.

The planner allocates whole shares, accounts for existing exposure and remaining
cash, and limits participation using the previous 20-bar average volume. Average
volume is derived from served `volume / volume_ratio`, preserving compatibility
with the current Druid schema. Missing liquidity, stale/future prices, excessive
volatility, recent known restructurings, occupied slots and insufficient budget
remain visible as rejection reasons. It can return **No suitable setup**.

Prices and stops are scenario references. Re-size at an actual entry price; a
gap can cause a stop to fill below its reference. Costs are explicit estimates,
not a broker-specific statutory fee calculator. Investor income/capital-gains
taxes, exchange price-limit/auction fill guarantees, and broker orders are absent.
Context indicators explain the observed setup, not the causal contribution of
each feature to the model. Scores are not converted to confidence percentages,
profit probabilities, expected-return ranges or guaranteed targets.

The **Model** page shows the latest completed retail experiment and whether the
production model records the current quality policy. A legacy model is labeled
as such; research candidates do not silently replace it.

## Adjusted-price consistency

Normal daily refreshes still resume per symbol from Iceberg. Watermark export
also supplies the earliest stored date and recent overlap closes. If the provider
restates historical prices across that overlap, ingestion refreshes that symbol
from its earliest stored date. It does not splice newly adjusted bars onto an old
adjustment scale. Failed reconciliation aborts the poll stage rather than publishing
the newly scaled interval alone.

Downloads explicitly request `auto_adjust=True, actions=True, repair=True` and
retain observed split/dividend metadata under
`ml/evaluation/results/market_data/actions/`. SciPy and scikit-learn are included
for the installed yfinance repair implementation. Provider repair is an estimate,
not certification. Splits/dividends are never applied a second time to adjusted
OHLC. References: [yfinance history](https://ranaroussi.github.io/yfinance/reference/yfinance.price_history.html),
[repair documentation](https://ranaroussi.github.io/yfinance/advanced/price_repair.html).

## Complex corporate actions

`config/corporate_actions.json` records sourced restructuring windows for Vedanta
and HEG. The HEG range is deliberately a conservative impact window including its
symbol transition; a record date is not asserted to be an exact trading ex-date.
The list is curated and incomplete: unknown actions can still affect data.

- Raw bars are retained. Indicators are unavailable within the restructuring
  window and restart afterward, including a fresh SMA-200 warm-up.
- Forward labels crossing an unresolved action window are unknown. All stocks
  use the benchmark's exit date rather than silently shifting missing-stock dates.
- If any universe constituent lacks a valid return, the universe target is
  withheld for that date. Training does not learn a demerger price drop as a loss.
- Null labels are removed **before** constructing rank targets. A null outcome
  cannot become a lowest-ranked training example.
- Publication joins scores to the same trade date and latest score cross-section.
  An excluded stock cannot retain an old rank in a fresh snapshot.
- Prospective cohorts crossing the event remain incomplete. Policy changes archive
  old reports under `forward/report_history/` before re-evaluation; predictions
  themselves remain immutable.

`unadjusted_wealth()` in `corporate_actions.py` can value actual holdings with
sourced split, dividend, merger and distributed-share terms. It requires genuinely
unadjusted contemporaneous prices; it refuses to value unpriced child entitlements.
The current Yahoo series is **not** relabeled as such a feed. Actual HEG/Vedanta
entitlement valuations remain unresolved. No fictitious correction factor or
selective deletion of large losses is used.

## Retraining and provenance

`scripts/retrain.sh` first rebuilds safe indicators from stored bars, then rebuilds
training data, trains and evaluates models. Silver/training tables record the
corporate-action policy digest. Training refuses a stale training-table digest;
model registries, selected models and evaluation results carry that provenance.
Unresolved-label dates are excluded consistently from model-selection metrics.

To activate changes with refreshed data:

1. Refresh market data with a Full pipeline run.
2. Use **Retrain model** to rebuild safe features/labels and select models.
3. Run the pipeline again to publish rankings from the newly selected model.

Existing production artifacts are not rewritten merely by opening the planner.
The current source implementation does not solve historical membership or
as-first-published statement availability; those need appropriate data sources.

## Portfolio simulator

`retail.simulate()` uses one capital account, integer shares, overlapping positions,
cash, stock/industry caps, signal-day liquidity, configurable entry/exit costs,
slippage, and daily mark-to-market drawdown. A date-t prediction enters at the next
benchmark-session close; stops are checked subsequently using open/low and gap
through stops fill at the worse opening price. Missing held prices, missing stop
execution inputs and unresolved entitlements make the experiment incomplete.
An incomplete strategy has no reported full-period return/drawdown; no missing
asset is silently sold at zero, carried as tradable, or discarded.

With provider-adjusted OHLC, share quantities and fills remain diagnostics rather
than a fully executable historical portfolio. A raw-price source, complete action
accounting, and a consistent total-return benchmark are prerequisites for a
validated retail performance claim.

## Controlled experiments

`retail_research.py` builds temporary features from stored bronze bars, applying
the same restructuring resets and safe label rules. It tests:

- `rank21`: 21-session rank ensemble.
- `sector21`: the same model with sector-relative three-month momentum.
- `rank63`: 63-session rank ensemble using the same fixed model settings.

Five expanding folds use a common 63-session embargo. The first three folds
select a candidate; the last two assess it. Selection requires a complete
validation result, at least 20 closed trades, and drawdown no worse than -25%,
then picks the highest validation return. Assessment does not choose the variant.
This is a fixed research protocol, not proof that its thresholds are optimal.

Prediction checkpoints, source snapshots and provenance are stored separately
from production models and prospective evidence. Bind local Spark to localhost
if the workstation's LAN address changes during a long run (the shared wrapper
now does this for local Spark masters):

```bash
source scripts/lib.sh
spark_submit "$ICEBERG_PKG" "$REPO_ROOT/ml/evaluation/retail_research.py" \
  --warehouse "$WAREHOUSE" --output "$REPO_ROOT/ml/evaluation/results/retail_research"
```

The initial run is in `ml/evaluation/results/retail_research_20261108/`.
Its archived simulator-v1 sources accompany the report; later execution-input
checks are stricter about missing OHLC. The source was previously researched,
so the assessment period is not a new untouched test. Survivorship, restatement,
adjusted-price execution and incomplete corporate-action coverage remain limits.
No automatic model promotion is enabled.

## Verification

Pure tests cover corporate-action wealth accounting, overlap reconciliation,
features/labels, allocation, cash, gap stops, missing prices and API validation.
The Spark integration test uses an isolated temporary warehouse and verifies
action-aware training plus suppression of stale published ranks. The live repair
smoke check fetches only an index and publishes no Kafka messages.

The next data-dependent work is verified entitlement valuation, an unadjusted
execution-price/total-return benchmark source, historical universe membership,
and genuine point-in-time quarterly statements. These cannot be inferred from
the current annual Yahoo snapshots. A historical calibration exercise and guarded
Forecast Lab are now implemented; both tested probability models failed the
baseline checks. Live estimates remain withheld. See
[forecast-calibration.md](forecast-calibration.md) for results and reproduction.
