export interface StockRow {
  symbol: string;
  company_name?: string;
  industry?: string;
  close?: number;
  change_percent?: number;
  rsi_14?: number;
  volume_ratio?: number;
  distance_from_52w_high?: number;
  distance_from_52w_low?: number;
  price_momentum_1m?: number;
  price_momentum_3m?: number;
  price_momentum_6m?: number;
  pe_ratio?: number;
  pb_ratio?: number;
  dividend_yield?: number;
  market_cap?: number;
  beta?: number;
  sma_50?: number;
  sma_200?: number;
  score_5d?: number;
  rank_5d?: number;
  score_21d?: number;
  rank_21d?: number;
  score?: number;
  rank?: number;
  label?: string;
  [key: string]: unknown;
}

export interface Overview {
  universe_size: number;
  advancers: number;
  decliners: number;
  unchanged: number;
  avg_change_percent: number;
  gainers: StockRow[];
  losers: StockRow[];
  sectors: Sector[];
}

export interface Sector {
  industry: string;
  count: number;
  avg_change_percent: number;
  advancers: number;
  decliners: number;
  avg_pe: number | null;
}

export interface PicksResponse {
  label: string;
  k: number;
  count: number;
  picks: StockRow[];
}

export interface ScreenerResponse {
  total: number;
  limit: number;
  offset: number;
  rows: StockRow[];
}

export interface UniverseResponse {
  size: number;
  industries: { industry: string; count: number }[];
}

export interface HistoryPoint {
  __time: string;
  close: number;
  sma_20: number | null;
  sma_50: number | null;
  sma_200: number | null;
  rsi_14: number | null;
  macd: number | null;
  macd_signal: number | null;
  volume: number | null;
}

export interface HistoryResponse {
  symbol: string;
  range: string;
  count: number;
  history: HistoryPoint[];
}

export interface BacktestMetric {
  net_sharpe: number | null;
  net_annualized_return: number | null;
  net_max_drawdown: number | null;
  gross_sharpe: number | null;
  avg_turnover: number | null;
  [key: string]: unknown;
}

export interface ModelMetric {
  ic_mean: number | null;
  ic_ir: number | null;
  ic_t_stat: number | null;
  sector_neutral_ic_mean: number | null;
  precision_at_k_vs_universe: number | null;
  precision_at_k_vs_index: number | null;
  rmse: number | null;
  top_k_mean_forward_return: number | null;
  universe_mean_forward_return: number | null;
  index_mean_forward_return: number | null;
  top_k_turnover: number | null;
  backtest?: BacktestMetric | null;
  [key: string]: unknown;
}

export interface WalkForwardMetric {
  n_periods?: number;
  ic_mean?: number | null;
  ic_t_stat?: number | null;
  net_sharpe?: number | null;
  [key: string]: unknown;
}

export interface ComparisonResponse {
  evaluated_at: string;
  k: number;
  cost_bps?: number;
  labels: Record<string, Record<string, ModelMetric | WalkForwardMetric>>;
}

export interface PipelineStep {
  id: string;
  state: string;
}

export interface PipelineRun {
  dag_id: string;
  dag_run_id: string;
  state: string;
  run_type: string;
  conf: Record<string, unknown>;
  start_date: string | null;
  end_date: string | null;
  active: boolean;
  steps: PipelineStep[];
  airflow_url: string;
}

export interface RunOptions {
  full: boolean;
  universe_limit?: number | null;
  publish_history: boolean;
}
