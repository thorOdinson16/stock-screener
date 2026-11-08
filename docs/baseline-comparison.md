# Controlled 21-session comparison

This experiment asks whether the walk-forward ranking model adds value over
simple 63-session momentum and equal weighting. It does not retrain, select or
promote a model. All inputs come from the existing historical research and its
matching price snapshot; this is not an untouched holdout.

## Fixed protocol

Every signal date starts with the same pool: stocks with an existing out-of-sample
21-session model score, a finite 63-session momentum signal, and signal-time
liquidity, volatility and corporate-action eligibility. Momentum requires the
exact 63-session common-calendar lookback; missing bars cannot silently extend
the lookback. There is no future-return eligibility filter.

The model selects its highest-scoring 20 stocks. Momentum selects its highest
63-session close returns, with no parameter search. Equal weighting selects all
eligible stocks. Ties resolve by symbol. The first mature entry in each period
starts a shared schedule: enter at the following session close, exit 21 sessions
after entry, and begin the next basket after the scheduled exit. Stops do not
trigger reinvestment before the next basket. No schedule offset is optimized.

All three portfolios start each period with ₹100,000 and use the same equal-target
allocator, whole shares, 8% stop, 0.5% planned risk per stock, 15% stock cap, 30%
industry cap and 0.1% average-volume participation limit. Uniform scaling preserves
equal targets when a cap binds; rounding and participation can change realized
weights. Fees and slippage are each 10 basis points per side. There is no leverage,
cash interest, investor tax or exchange auction/limit-fill model.

The research position ceiling is 1,000 for every strategy so that the all-eligible
baseline is allowed. This differs from the dashboard's ten-position strategy;
these results cannot be substituted for that strategy's backtest. The model and
momentum baskets still select at most 20 stocks. Stops cannot execute on the entry
bar; subsequent gap stops fill at the worse of the open and stop reference.

An additional **equal-notional selection diagnostic** uses fractional baskets
with the same entries, exits, stops and proportional costs. It removes allocation,
whole-share, industry and participation constraints to expose stock selection
separately from cash deployment. It is not an executable retail portfolio. Any
missing selected outcome invalidates its cycle rather than dropping that stock.

## Portfolio results

| Period | Strategy | Net return after costs | Max drawdown | Mean entry exposure |
|---|---|---:|---:|---:|
| Earlier: 2024-04-05–2025-10-14 | Model | 21.62% | -23.12% | 88.90% |
| Earlier | Momentum | -8.80% | -14.46% | 65.07% |
| Earlier | All eligible, equal targets | -0.46% | -4.55% | 13.57% |
| Later: 2025-10-15–2026-10-26 | Model | 11.75% | -7.71% | 48.40% |
| Later | Momentum | 17.68% | -5.18% | 39.84% |
| Later | All eligible, equal targets | 7.49% | -3.60% | 17.72% |

The earlier period contains 17 non-overlapping cycles; the later period contains
11. These are cumulative period returns, not annualized returns. Different cash
deployment makes the all-eligible portfolio an allocation baseline, not a pure
test of ranking quality. Many basket targets cannot purchase an expensive whole
share with ₹100,000. Industry caps can also leave substantial cash.

In the later portfolio test, the model's mean cycle return minus momentum was
-0.42 percentage points, with a descriptive 95% paired-bootstrap interval from
-1.73 to +1.12 percentage points. Against equal weighting the difference was
+0.44 points, with an interval from -1.16 to +2.13 points. Both span zero; the
experiment does not establish an advantage. Momentum performed better in this
later sample, but this is insufficient evidence to promote it either.

The equal-notional diagnostic also favors momentum in the later period:

| Strategy | Earlier net return | Later net return |
|---|---:|---:|
| Model | 28.36% | 15.64% |
| Momentum | -13.88% | 29.83% |
| All eligible | Unavailable: one incomplete cycle | 8.01% |

The later equal-notional model-minus-momentum mean cycle difference is -1.03
percentage points, with a descriptive interval from -3.20 to +1.14 points.
Against equal weighting it is +0.68 points, with an interval from -0.34 to +1.92
points. Both still span zero. The earlier all-eligible diagnostic has 16 of 17
complete cycles and no full-period return; its partial paired measurements must
not be treated as a complete benchmark. Two selected outcomes in the basket
signalled on 2025-01-23 lack required prices. This remains unavailable even though
whole-share allocation avoided those exposures in the capital book.

The intervals resample paired non-overlapping cycles 2,000 times with seed 42.
They are descriptive: adjacent market regimes may still be dependent. No candidate
is selected using these results. The source history was already researched.

## Data and reproducibility

Prices remain provider-adjusted, and financial/universe inputs retain survivorship
and restatement limits. Current industry metadata is used. Corporate entitlements
and missing held prices stop valuation explicitly; unknown returns are never
converted to losses or omitted from an ostensibly complete result. Completed
partial comparisons, when present, are labelled separately from full-period ones.

With HDFS running:

```bash
bash scripts/compare_baselines.sh
```

Optional arguments are the research directory and output directory. Outputs live
in `ml/evaluation/results/baseline_comparison_20261108/`: `config.json`, detailed
`validation.json`/`assessment.json`, `report.json`, and `summary.md`. The manifest
records source hashes, prediction hashes, price snapshot, event digest, universe
metadata, boundaries and policy. A directory with a different protocol is refused.

`GET /api/research/baselines` serves the report in Forecast Lab. Changes to the
algorithm, prediction evidence, research configuration, universe or event ledger
mark it stale. This endpoint reads local historical evidence and does not query
the current market or alter the production model.

Tests cover common eligibility, ranking rules, shared chronology, non-overlap,
costs, gap stops, equal targets and limits, missing entries, unavailable future
entitlements, diagnostic accounting and stale-report guards.
