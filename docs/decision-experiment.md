# Sector-relative targets, abstention and horizon experiment

The experiment is complete. **Neither horizon produced a candidate that passed
the frozen advancement rule.** Selective sector-relative signals reduced
exposure and drawdown, but 21-session selection did not beat exposure-matched
momentum. The 63-session selective variant is a research lead with very little
independent evidence, not a qualified replacement.

Production, retraining, the Windows/Docker pipeline and the existing prospective
paper trial are unchanged. No new prospective model was installed.

## Earnings-expectations source audit

The repository contains original reported results, not a verified history of
pre-announcement analyst consensus or estimate revisions. The source audit
records **zero consensus snapshots and zero estimate-revision records**. The
surprise-feature backtest was therefore **not run**. There was no subscription
purchase, account connection or current-estimate backdating.

[S&P Global](https://www.spglobal.com/market-intelligence/en/solutions/capital-iq-estimates)
documents historical consensus, comparison against pre-report expectations and
point-in-time snapshots. [LSEG](https://developers.lseg.com/en/api-catalog/refinitiv-data-platform/estimates-API)
documents historical I/B/E/S estimates. These are possible sources, not verified
access or confirmed coverage for this pilot. The audit's direct S&P page archive
request returned 403; browser retrieval read its documentation. That fetch error
is retained in the source audit. LSEG and Infosys source snapshots were archived.
The Firecrawl connector also returned an unavailable-tool error; web retrieval
was used instead. No provider data records were inferred from documentation.

An original Infosys exchange attachment already held by the pilot contains the
revised FY23 revenue-guidance range on PDF pages 24 and 31. Its source is
[the original NSE attachment](https://nsearchives.nseindia.com/corporate/InfyBM12012023_12012023153816.pdf),
disseminated 12 January 2023 at 15:38:23 IST according to the archived exchange
receipt. This establishes one historical guidance example, not uniform
multi-issuer coverage. The [dated call transcript](https://www.infosys.com/investors/reports-filings/quarterly-results/2022-2023/q3/documents/transcripts/press-conference.pdf)
contains prior and revised guidance, but a call date does not establish when its
transcript was published. The current [guidance-versus-actuals table](https://www.infosys.com/investors/reports-filings/financials/guidance-vs-actuals-usd.html)
contains subsequently known results and is not a substitute for a historical
revision archive. None of these guidance sources was added to predictive inputs.

A usable consensus dataset must establish security identity, fiscal period,
metric/accounting definition, scope, currency/units, contributor count and a
verifiable snapshot timestamp before the original announcement. Actual results
must use the same metric and the original publication time. A single broker
forecast is not automatically consensus. Current estimates and later actuals
cannot be reconstructed into pre-announcement expectations.

## What was actually tested

The new runner freezes its protocol and source/input hashes before fitting. It
uses the prior 20-stock pilot's verified financial features, technical indicators
and sector context. Architecture and parameters remain fixed: the existing
LightGBM 7-leaf, 100-tree model, training-only median imputation with missing
indicators, and no hyperparameter search.

The independent dimensions are:

| Dimension | Fixed alternatives |
|---|---|
| Horizon | 21 or 63 trading sessions |
| Target | Stock execution net return; or that return minus sector return |
| Trading rule | Rank eligible stocks; or require a positive economic forecast |

The sector benchmark is a gross, passive, equal-weight industry return from
next-session close to the same scheduled exit close. It excludes the stock
itself. It is subtracted from the stock's stop/cost-adjusted execution return to
form the relative training target. Execution remains long-only; this is not a
sector-hedged portfolio.

Peers need 63 consecutive valid closes through the signal date. The denominator
uses only that past availability within the existing current-universe industry
map. It requires at least five other stocks and 95% known future benchmark
outcomes when constructing training labels. A later missing price or unresolved
restructuring does not shrink the denominator or become a zero return. Missing
benchmark-calendar observations remain unknown.

Two initial data-check attempts, preserved as versions 1 and 2, stopped before any
model fit. They exposed missing benchmark dates and a denominator that included
companies without sufficient signal-date history. Version 3 corrects that
population rule without lowering the five-peer/95% requirements. No outcome or
model-performance result was used to make those corrections.

Both models train on identical rows with known, matured absolute and relative
targets. The original five chronological folds and 63-session embargo are retained;
scheduled target maturity must also precede the test date. Even a stock stopped
out early must wait until its scheduled holding window matures. Prediction rows
are never filtered using future outcomes.

The selective rule is fixed at an **absolute forecast net return strictly above
1%**. Relative ranking additionally requires a relative forecast above zero.
This is a regression forecast threshold, not a calibrated probability or
confidence level. No threshold was searched or changed after seeing results.

Controls are the newly trained same-horizon absolute model and momentum. The
recorded production 21-session scores are an additional 21-session control. There
is no invented production 63-session forecast.

There are 20 model fits: two targets, two horizons and five folds. Trading rules
reuse these predictions. Each horizon retains all strategy/scenario results,
including failed and cash-only opportunities.

## Evaluation and decision rules

Three validation folds cover 5 April 2024–14 October 2025. Two later assessment
folds cover 15 October 2025–23 October 2026. These periods have already been
researched; neither is genuinely fresh evidence.

Top-five baskets use chronological nonoverlapping cycles and one fifth of capital
per selected stock. Unfilled slots stay in cash earning zero. Separate matched
controls take the same number of stocks on the exact same cycle dates, preventing
an apparent selection advantage from merely reducing exposure. Cash-only cycles
remain in the schedule but do not count as active evidence. Missing selected
outcomes remain incomplete.

The actual ₹100,000 ten-position planner is evaluated separately, including
whole shares, risk/industry/position constraints, an 8% stop and next-close entry.
All three fixed execution scenarios are retained: 10 bps fees plus 10 bps slippage
per side, doubled costs, and one-session delayed entry. Full retail ledgers,
selected stocks, outcomes, exposures and comparisons are saved.

A candidate must have positive baskets and retail equity under every scenario,
at least eight complete active validation cycles, complete positive ordinary and
exposure-matched basket advantages over all controls, positive retail advantages,
positive basket advantages in at least two validation folds, at least 20 closed
retail trades and drawdown no worse than 25%. Selection is recorded from
validation before later evaluation. No automatic promotion is permitted.

## Actual retail results

These are net returns over each whole period, not annualized returns:

| Horizon / strategy | Validation | Later assessment |
|---|---:|---:|
| 21 / absolute target, rank eligible stocks | +2.96% | −1.85% |
| 21 / absolute target, selective | +0.46% | −2.46% |
| 21 / sector-relative target, rank eligible stocks | +6.48% | −5.83% |
| 21 / sector-relative target, selective | +8.18% | −4.18% |
| 21 / momentum | −4.70% | −2.59% |
| 21 / recorded production model | −0.75% | −6.64% |
| 63 / absolute target, either rule | +3.24% | −1.75% |
| 63 / sector-relative target, rank eligible stocks | +5.41% | −5.83% |
| 63 / sector-relative target, selective | +6.90% | −1.03% |
| 63 / momentum | −0.76% | −1.93% |

These absolute controls are newly trained on the shared valid-label rows. They
are not the previous experiment's unchanged prediction files. The production
control is separately labelled.

For the selective sector-relative strategy:

| Diagnostic | 21-session validation / later | 63-session validation / later |
|---|---:|---:|
| Active independent cycles | 16 / 10 | 4 / 3 |
| Total scheduled cycles | 17 / 11 | 5 / 3 |
| Retail mean cash fraction | 80.0% / 79.3% | 84.7% / 91.2% |
| Retail maximum drawdown | 3.05% / 5.27% | 2.65% / 4.52% |
| Retail return at double costs | +6.60% / −5.02% | +6.23% / −1.22% |
| Retail return with delayed entry | +9.07% / −1.13% | +7.59% / −1.21% |

Lower drawdown is useful behavior, but much of the risk reduction accompanies
higher cash holdings. It cannot alone establish better prediction.

The 21-session selective strategy's top-five difference against exposure-matched
momentum is **−0.055 percentage points per scheduled validation cycle**, and
**−0.667 percentage points** later. Those differences are worse than zero in the
observed sample, although descriptive intervals include zero. It fails the fixed
advancement rule despite the attractive validation retail return.

The 63-session selective strategy passes the other validation comparisons in this
run, but has only **four active validation cycles**, below the unchanged minimum
of eight. Its later exposure-matched difference is +1.784 percentage points per
cycle, based on only three cycles, with a descriptive 95% interval of −1.444 to
+6.798 percentage points. That is far too uncertain to establish an edge. Its
later retail return is still negative.

## Interpretation and reproducibility

The practical research lead is selective exposure, particularly at the longer
horizon. The 21-session result does not establish improved stock selection; the
63-session result requires substantially more independent evidence. Neither
qualifies as a stronger production predictor. Existing paper models stay frozen.

Limitations include the small 20-stock sample, current-universe/sector taxonomy,
survivorship, adjusted prices, inherited legacy finite-row restrictions,
simplified execution/taxes, and repeated research on these dates. Bootstrap
intervals are descriptive and not corrected for all prior experiments. Retail
terminal equity can include marked open positions; zero remained at the endpoints
of this particular run. Cycles and retail equity are different evaluations and
must not be combined into a purported account return.

```bash
bash scripts/decision_experiment.sh
```

For a new version, pass a different output directory as the first argument.
A completed run verifies hashes and returns its saved result; it does not retrain.
Original archived pilot and tournament artifacts must be present. No network or
paid data is required to verify a completed run.

The completed artifacts are under
`ml/evaluation/results/decision_experiment_20261109_v3/`: source audit, protocol,
per-horizon targets/features, fold predictions and training/maturity receipts,
validation-only selections, full retail ledgers, and `report.json`. Original
production/paper hashes are checked before and after the run. Runtime data stays
ignored by Git; source code, tests and this report are repository files.
