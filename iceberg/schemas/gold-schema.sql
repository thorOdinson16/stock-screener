-- Gold layer: daily scored/ranked model output + materialized top picks.
-- Produced by the Spark MLlib training/scoring jobs (ml/training/train_model.py,
-- spark/jobs/score_stocks.py). See docs/project-spec.md §11.1, §14.

CREATE NAMESPACE IF NOT EXISTS gold;
CREATE NAMESPACE IF NOT EXISTS ml;

-- One row per (symbol, trade_date, label). `label` is the forward-return horizon
-- the score targets (fwd_ret_5d / fwd_ret_21d). Rows accumulate across days so
-- Druid can serve score-trend-over-time queries.
CREATE TABLE IF NOT EXISTS gold.stock_scores (
  symbol STRING NOT NULL,
  trade_date DATE NOT NULL,
  label STRING NOT NULL,
  score DOUBLE,
  rank INT,
  model_name STRING,
  model_version STRING,
  scored_at TIMESTAMP NOT NULL
)
USING iceberg
LOCATION '/warehouse/gold/stock_scores'
TBLPROPERTIES (
  'format-version'='2',
  'write.parquet.compression-codec'='snappy'
);

-- Materialized top-N picks per (trade_date, label).
CREATE TABLE IF NOT EXISTS gold.top_picks (
  trade_date DATE NOT NULL,
  label STRING NOT NULL,
  rank INT NOT NULL,
  symbol STRING NOT NULL,
  score DOUBLE,
  model_name STRING,
  scored_at TIMESTAMP NOT NULL
)
USING iceberg
LOCATION '/warehouse/gold/top_picks'
TBLPROPERTIES (
  'format-version'='2',
  'write.parquet.compression-codec'='snappy'
);

-- Reproducible, point-in-time training set built by spark/jobs/build_training.py.
-- Stores features, both forward-return labels, the two benchmark returns, and the
-- train/embargo/test assignment. Kept in Iceberg so experiments are replayable
-- via time travel (docs/project-spec.md §10, §26).
CREATE TABLE IF NOT EXISTS ml.training_dataset (
  symbol STRING NOT NULL,
  trade_date DATE NOT NULL,

  close DOUBLE,
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

  fwd_ret_5d DOUBLE,
  fwd_ret_21d DOUBLE,

  universe_ret_5d DOUBLE,
  universe_ret_21d DOUBLE,
  index_ret_5d DOUBLE,
  index_ret_21d DOUBLE,

  split STRING NOT NULL
)
USING iceberg
LOCATION '/warehouse/ml/training_dataset'
TBLPROPERTIES (
  'format-version'='2',
  'write.parquet.compression-codec'='snappy'
);
