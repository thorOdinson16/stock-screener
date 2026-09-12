-- Unpartitioned: the SeaTunnel Iceberg sink keeps one open file per partition
-- per checkpoint. Partitioning by symbol (500 values) meant ~500 tiny files
-- flushed concurrently to the single HDFS DataNode every checkpoint, stalling
-- commits and timing them out. See docs/project-spec.md §11.2 for partition
-- evolution experiments (add a coarser partition such as days(ingested_at)).
CREATE TABLE IF NOT EXISTS bronze.market_quotes (
  symbol STRING NOT NULL,
  timestamp TIMESTAMP NOT NULL,
  open DECIMAL(18, 2),
  high DECIMAL(18, 2),
  low DECIMAL(18, 2),
  close DECIMAL(18, 2),
  volume BIGINT,
  previous_close DECIMAL(18, 2),
  change_percent DOUBLE,
  market_cap DECIMAL(20, 2),
  sector STRING,
  industry STRING,
  exchange STRING,
  ingested_at TIMESTAMP NOT NULL
)
USING iceberg
LOCATION '/warehouse/bronze/market_quotes'
TBLPROPERTIES (
  'format-version'='2',
  'write.parquet.compression-codec'='snappy'
);

-- Market Fundamentals — P/E, EPS, dividend yield, beta, growth, etc.
CREATE TABLE IF NOT EXISTS bronze.market_fundamentals (
  symbol STRING NOT NULL,
  timestamp TIMESTAMP NOT NULL,
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
  earnings_growth DOUBLE,
  ingested_at TIMESTAMP NOT NULL
)
USING iceberg
LOCATION '/warehouse/bronze/market_fundamentals'
TBLPROPERTIES (
  'format-version'='2',
  'write.parquet.compression-codec'='snappy'
);