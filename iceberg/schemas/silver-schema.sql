-- Silver layer: cleaned / feature-enriched tables produced by Spark.

CREATE NAMESPACE IF NOT EXISTS silver;

-- Technical indicators + derived features per symbol per trading day.
-- Computed by spark/jobs/compute_indicators.py from bronze.quotes_daily.
CREATE TABLE IF NOT EXISTS silver.quotes_enriched (
  symbol STRING NOT NULL,
  trade_date DATE NOT NULL,

  close DECIMAL(18, 2),
  volume BIGINT,

  sma_20 DOUBLE,
  sma_50 DOUBLE,
  sma_200 DOUBLE,

  ema_12 DOUBLE,
  ema_26 DOUBLE,

  rsi_14 DOUBLE,

  macd DOUBLE,
  macd_signal DOUBLE,

  volatility_20d DOUBLE,

  volume_avg_20d DOUBLE,
  volume_ratio DOUBLE,

  distance_from_52w_high DOUBLE,
  distance_from_52w_low DOUBLE,

  price_momentum_1m DOUBLE,
  price_momentum_3m DOUBLE,
  price_momentum_6m DOUBLE,

  computed_at TIMESTAMP NOT NULL
)
USING iceberg
TBLPROPERTIES (
  'format-version'='2',
  'write.parquet.compression-codec'='snappy'
);

-- Latest cleaned fundamentals snapshot per symbol (one row per symbol).
CREATE TABLE IF NOT EXISTS silver.fundamentals_clean (
  symbol STRING NOT NULL,
  updated_at TIMESTAMP NOT NULL,

  pe_ratio DOUBLE,
  pb_ratio DOUBLE,
  eps DOUBLE,
  dividend_yield DOUBLE,
  market_cap DECIMAL(20, 2),
  beta DOUBLE,
  fifty_two_week_high DOUBLE,
  fifty_two_week_low DOUBLE,
  return_on_equity DOUBLE,
  debt_to_equity DOUBLE,
  revenue_growth DOUBLE,
  earnings_growth DOUBLE
)
USING iceberg
TBLPROPERTIES (
  'format-version'='2',
  'write.parquet.compression-codec'='snappy'
);

-- Annual financial statements, cleaned: the latest fetch per (symbol, period_end),
-- with the prior fiscal year's revenue / net income attached for growth rates.
-- Computed by spark/jobs/compute_indicators.py from bronze.financial_statements.
-- Point-in-time use (a publication lag after period_end) is applied by the model
-- features (ml/feature_engineering/asof.py), not here.
CREATE TABLE IF NOT EXISTS silver.financial_statements (
  symbol STRING NOT NULL,
  period_end DATE NOT NULL,
  total_revenue DOUBLE,
  net_income DOUBLE,
  diluted_eps DOUBLE,
  stockholders_equity DOUBLE,
  total_debt DOUBLE,
  shares_outstanding DOUBLE,
  prev_total_revenue DOUBLE,
  prev_net_income DOUBLE,
  fetched_at TIMESTAMP NOT NULL
)
USING iceberg
TBLPROPERTIES (
  'format-version'='2',
  'write.parquet.compression-codec'='snappy'
);
