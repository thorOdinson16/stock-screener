# Model research: fundamentals and extra technical features (2026-10)

Follow-up to the closing decision in `docs/project-status.md` §3, where the model's
edge did not hold up across time (21d walk-forward t = 0.41, below the agreed bar of
t > 2). This round tested the levers listed there as not yet pursued.

## Method

- **Data.** 5 years of daily bars for the NIFTY 500 (490 symbols with features after
  warm-up, 1,041 trading dates), plus annual financial statements from yfinance:
  2,240 statements, ~4.5 fiscal years per company (FY2022 onward).
- **Point-in-time fundamentals.** A fiscal year's statement is used only from
  `period_end + 90 days`. SEBI requires annual results within 60 days, so this
  adds a safety margin. Ratios use the price on each trade date. Features: earnings
  yield, book-to-price, ROE, debt/equity, revenue growth and log market cap.
  Code: `ml/feature_engineering/extra_features.py`.
- **Candidate technical features:** short-term reversal (5d), 12-1 momentum, volume
  trend, 120d beta and idiosyncratic volatility against NIFTY 50, max daily return
  (21d), Amihud illiquidity, and 3-month momentum relative to the industry.
- **Evaluation:** `ml/evaluation/research.py`. This is the production walk-forward
  (5 expanding folds, 21-day embargo) with fixed model specs on each feature set, so
  the comparison isn't confounded by model selection. IC is the daily Spearman
  correlation with the excess return; t uses Newey–West errors. Net Sharpe comes
  from the long/short top/bottom-20 backtest at 10 bps per side. Runs are recorded
  in `ml/experiments/*_research.json`.

Feature sets: **base** = the 14 technical features in production before this
round; **tech** = base + the candidate technical features; **fund** = base +
fundamentals; **all** = everything.

## Results

### Screen: linear model, all horizons

| horizon | base | tech | fund | all |
|---|---|---|---|---|
| 5d IC (t) | −0.005 (−0.55) | −0.006 (−0.58) | +0.002 (0.22) | +0.001 (0.06) |
| 21d IC (t) | −0.007 (−0.38) | +0.004 (0.20) | **+0.020 (1.07)** | +0.020 (1.00) |
| 63d IC (t) | −0.014 (−0.42) | +0.016 (0.41) | **+0.045 (1.34)** | +0.044 (1.28) |

### Confirmation: production rank ensemble (GBT-rank + RF-rank), 21d

| features | IC | t | folds positive | net Sharpe | fold ICs |
|---|---|---|---|---|---|
| base | +0.017 | 1.06 | 3/5 | 0.49 | −0.024, −0.047, +0.025, +0.072, +0.065 |
| **fund** | **+0.043** | **3.88** | **5/5** | **1.67** | +0.011, +0.013, +0.094, +0.085, +0.007 |
| all | +0.042 | 3.12 | 4/5 | 1.57 | −0.013, +0.019, +0.097, +0.094, +0.004 |

## Decision

- **Adopted: fundamentals** (`fund`). They are the first change to clear the
  walk-forward bar (t = 3.88, every fold positive). Most of the gain is in the early
  folds, where the technical-only model was negative.
- **Not adopted: the extra technical features.** They add nothing on top of
  fundamentals and make the folds less stable (`all` vs `fund`). The code stays in
  `extra_features.py` for research.
- **Not adopted: a 63-day horizon.** Fundamentals help most there, but adopting it
  means changing the serving layer and dashboard. It's the obvious next step.
- **5d** gets no usable signal from any feature set. It keeps the same features as
  21d (one shared feature list) and is reported as weak.

## Caveats

- **Multiple testing.** About 15 configurations were tried. A t of 3.88 holds up
  under any reasonable correction, but this isn't a single pre-registered test.
- **Possible restatements.** yfinance may serve restated rather than as-first-
  published figures, which could flatter fundamentals slightly despite the 90-day
  lag. Point-in-time vendor data would remove this.
- **Survivorship bias.** The universe is today's NIFTY 500, as before.
- **Short fundamentals history.** Rows before mid-2022 have no statement
  (fundamentals are neutral there), so the early folds rely on fewer
  fundamental-informed rows.
