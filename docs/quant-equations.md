# Fixed quantitative equations

Six mathematical ranking rules were implemented and tested on the same
21-session strategy. No equation lookback or blend weight was optimized. The
protocol was frozen before evaluating the results; this already researched
history is not an untouched holdout.

Let P be the provider-adjusted close, r its daily log return, m the NIFTY 50 daily
log return, and sigma the sample standard deviation. Windows use the common
benchmark calendar. A signal computed at today's close enters at the following
session close.

| Rule | Equation and interpretation |
|---|---|
| 12-minus-1 momentum | log(P[t-21]/P[t-252]): prior-year trend excluding the latest month |
| Volatility-adjusted momentum | log(P[t-21]/P[t-252]) / (sigma63(r) sqrt(231)): trend relative to recent daily noise |
| Market-residual momentum | sum63(r-alpha252-beta252 m) / (sigma252(residual) sqrt(63)): recent residual drift relative to its estimated noise |
| Mean reversion | -(log(P[t])-mean63(log(P))) / sigma63(log(P)): rank prices below their recent log-price average higher |
| Breakout strength | log(P[t]/max(P[t-63:t-1])) / sigma63(r): distance above/below the prior price range, scaled by volatility |
| Fixed rank blend | Equal average of percentile ranks of 12-minus-1 momentum, negative volatility, and sector-relative 63-session momentum |

For the market-residual rule, beta252 = cov252(r,m)/var252(m), and alpha252 =
mean252(r)-beta252 mean252(m). Both coefficients are estimated using only the
252 returns available on the signal date. The last 63 residuals are evaluated
using those coefficients. This is a single-factor residual-drift proxy; it is not
a replication of a published multifactor residual-momentum portfolio.

The sector component uses leave-one-out industry momentum; a singleton industry
receives a fixed neutral relative signal. Industry membership is the current
taxonomy, an explicit historical-data limitation. Zero/near-zero risk denominators,
missing prices and unresolved structural-action lookbacks produce unavailable
scores, never infinite conviction or filled-forward prices.

## Controlled experiment

All six rules and controls share a complete 253-close calendar history and finite
signal availability. The resulting sample has 281,849 rows across 630 signal
dates and 479 stocks, retaining 98.27% of the original reference prediction rows.
No future outcome is used to decide this pool. That stricter sample explains why
its momentum control differs from earlier experiments' returns.

The allocator starts each period with INR100,000, ten positions, whole shares,
stock/industry/risk caps, liquidity restrictions, an 8% stop, and fees plus slippage
each 10 basis points per side. Stress tests double both costs or add one session
of entry delay. Additional equal-notional diagnostics remove allocation effects.
Missing corporate-action wealth remains incomplete.

Earlier validation is 2024-04-05 through 2025-10-14. Later assessment is
2025-10-15 through 2026-10-26. Selection uses only earlier net portfolio results,
rank consistency, drawdown/trade-count requirements and cost/delay checks.

## Actual results

These are cumulative portfolio returns after modeled costs, not annual rates.

| Rule | Earlier net | Later net | Later matched momentum | Later drawdown |
|---|---:|---:|---:|---:|
| 12-minus-1 momentum | -9.89% | 28.11% | 34.70% | -7.76% |
| Volatility-adjusted momentum | 5.11% | 24.60% | 34.70% | -8.88% |
| Market-residual momentum | 9.13% | 5.00% | 34.70% | -12.76% |
| Mean reversion | 8.03% | 0.59% | 34.70% | -8.12% |
| Breakout strength | 9.27% | -4.25% | 34.70% | -11.31% |
| Fixed rank blend | 0.48% | -2.70% | 34.70% | -7.45% |

The earlier validation gate selected market-residual momentum. It did not select
the best later result retrospectively. That choice then returned 5.00% in later
assessment, 2.65% under doubled costs, and 3.37% with delayed entry. All were
below their matched momentum controls.

No formula established a positive advantage. Paired equal-notional cycle
comparisons use 2,000 seeded bootstrap draws with a nominal familywise adjustment
within the six equation tests. Their intervals all include zero. Mean reversion's
full equal-notional comparison additionally has an unavailable cycle. Adjacent
market regimes remain dependent; these intervals are descriptive, not guarantees.
This adjustment also does not erase the project's earlier multiple testing.

The current-model reference is incomplete at an unresolved corporate-action
window and has no asserted full-period return. No formula is promoted and no
existing frozen paper prediction, model or production ranking is changed.

## Inspect and reproduce

```bash
bash scripts/quant_equations.sh
```

The runner uses the existing isolated ML environment and local frozen snapshot;
HDFS is not needed. Optional positional arguments choose input snapshot, base
tournament and output directory. Completed runs are read-only.

Artifacts live in `ml/evaluation/results/quant_equations_20261109/`: protocol,
validation-only selection, later signal files, detailed simulations, robustness
diagnostics, `report.json` and `summary.md`. Source/data changes mark dashboard
evidence stale. Forecast Lab reads `GET /api/research/equations`.

Tests check the exact skipped-month identity, the residual equation against an
explicit trailing OLS calculation, causal windows, missing-data/zero-risk behavior,
prior-high breakouts and rejection of incomplete comparisons.

## Research context

These tests are custom simplified signals, not reproductions of the portfolios
or evidence in the following research:

- [Jegadeesh and Titman: Returns to Buying Winners and Selling Losers](https://doi.org/10.1111/j.1540-6261.1993.tb04702.x)
- [Moskowitz, Ooi and Pedersen: Time Series Momentum](https://www.aqr.com/Insights/Research/Journal-Article/Time-Series-Momentum)
- [Blitz, Huij and Martens: Residual Momentum](https://pure.eur.nl/ws/files/46882404/ResidualMomentum-2011.pdf)

Academic evidence in other universes or implementations does not establish a
trading edge for this dataset, cost model or retail strategy. Adjusted execution
prices, current-universe survivorship and unresolved entitlements remain limits.
