export type Numeric = number | string;

export type PlatformUser = {
  id: number;
  username: string;
  display_name: string;
  status: "ACTIVE" | "DISABLED";
  role: "ADMIN" | "TRADER" | "VIEWER";
  role_name: string;
  permissions: string[];
  created_at: string | null;
  updated_at: string | null;
};

export type LoginResponse = {
  access_token: string;
  token_type: "bearer";
  expires_in: number;
  user: PlatformUser;
};

export type AccountSummary = {
  id: string;
  user_id: string;
  currency: string;
  initial_balance: Numeric;
  cash_balance: Numeric;
  used_margin: Numeric;
  available_funds: Numeric;
  realized_pnl: Numeric;
  unrealized_pnl: Numeric;
  total_fees: Numeric;
  equity: Numeric;
  status: string;
  updated_at: string | null;
};

export type TradeSignal = {
  id: string;
  symbol: string;
  timeframe: string;
  pattern: "head_shoulders_top" | "inverse_head_shoulders";
  alert_type: string;
  score: number;
  message: string;
  created_at: string | null;
  direction: "LONG" | "SHORT";
  suggested_entry_price: Numeric | null;
  suggested_stop_price: Numeric | null;
  suggested_take_profit_price: Numeric | null;
  suggested_target_price: Numeric | null;
  risk_reward_ratio: Numeric;
  last_price: Numeric | null;
  quote_updated_at: string | null;
  quote_fresh: boolean;
  tradeable: boolean;
  tradeable_reason: string | null;
  expires_at: string | null;
  signal_payload: {
    pattern_score?: number | null;
    pattern_grade?: string;
    trend_label?: string;
    [key: string]: unknown;
  };
};

export type PositionLot = {
  id: string;
  symbol: string;
  side: "LONG" | "SHORT";
  open_price: Numeric;
  last_price: Numeric;
  original_quantity: number;
  remaining_quantity: number;
  margin: Numeric;
  unrealized_pnl: Numeric;
  realized_pnl: Numeric;
  stop_price: Numeric | null;
  take_profit_price: Numeric | null;
  opened_at: string;
  quote_updated_at: string | null;
};

export type PaperOrder = {
  id: string;
  symbol: string;
  side: "BUY" | "SELL";
  position_effect: "OPEN" | "CLOSE";
  position_side: "LONG" | "SHORT";
  quantity: number;
  status: string;
  source: string;
  requested_price: Numeric | null;
  filled_price: Numeric | null;
  created_at: string;
  filled_at: string | null;
  position_lot_id?: string | null;
};

export type LedgerEntry = {
  id: number;
  entry_type: string;
  amount: Numeric;
  balance_after: Numeric;
  description: string;
  created_at: string;
};

export type ContractSpec = {
  symbol: string;
  exchange: string;
  name: string;
  multiplier: Numeric;
  price_tick: Numeric;
  margin_rate: Numeric;
  fee_mode: "TURNOVER_RATE" | "PER_LOT";
  fee_value: Numeric;
  fee_close_today_mode: "TURNOVER_RATE" | "PER_LOT" | null;
  fee_close_today_value: Numeric | null;
  enabled: boolean;
};

export type ProductCatalogItem = {
  symbol: string;
  exchange: string;
  name: string;
  representative_symbol: string;
};

export type ProductDetails = {
  symbol: string;
  exchange: string;
  name: string;
  multiplier: Numeric;
  price_tick: Numeric;
  margin_rate?: Numeric;
  fee_mode?: "TURNOVER_RATE" | "PER_LOT";
  fee_value?: Numeric;
  fee_close_today_mode?: "TURNOVER_RATE" | "PER_LOT" | null;
  fee_close_today_value?: Numeric | null;
  fee_description?: string;
};

export type ProductCostImportResult = {
  imported: number;
  errors: Array<{ row: number; reason: string }>;
};

export type MarketQuote = {
  symbol: string;
  last_price: Numeric;
  source: string;
  market_time: string | null;
  updated_at: string | null;
};

export type BacktestRule = {
  key: string;
  label: string;
  type: "PATTERN_TARGET" | "RR" | "QTR";
  multiplier?: number | null;
};

export type BacktestRequest = {
  name?: string;
  symbols: string[];
  timeframes: string[];
  kline_count: number;
  max_holding_bars?: number | null;
  initial_capital: number;
  position_sizing_mode: "PERCENT" | "FIXED_LOTS";
  single_symbol_position_pct?: number;
  single_symbol_lots?: number;
  no_overnight: boolean;
  entry_conditions: Array<
    | "head_shoulders_top:right_shoulder_confirmed"
    | "inverse_head_shoulders:right_shoulder_confirmed"
    | "head_shoulders_top:right_neck_confirmed"
    | "inverse_head_shoulders:right_neck_confirmed"
  >;
  other_entry_conditions: Array<
    | "head_shoulders_top:head_shoulders_top_pullback"
    | "inverse_head_shoulders:inverse_head_shoulders_pullback"
  >;
  min_pattern_score: number;
  min_trend_score: number;
  other_min_pattern_score: number;
  other_max_trend_score: number;
  stop_loss_qtr_multiplier: number;
  take_profit_rules: BacktestRule[];
};

export type BacktestSymbolGroup = {
  id: string;
  name: string;
  symbols: string[];
  created_at: string;
  updated_at: string;
};

export type BacktestSymbolGroupPayload = Pick<BacktestSymbolGroup, "name" | "symbols">;

export type BacktestSummary = {
  id: number;
  rule_key: string;
  rule_label: string;
  entry_condition: string;
  rule_type: BacktestRule["type"];
  multiplier: Numeric | null;
  sample_count: number;
  wins: number;
  losses: number;
  breakevens: number;
  incomplete: number;
  take_profit_hits: number;
  stop_hits: number;
  time_exits: number;
  win_rate: Numeric;
  gross_pnl: Numeric | null;
  net_pnl: Numeric | null;
  avg_r: Numeric;
  total_r: Numeric;
  profit_factor: Numeric | null;
  avg_holding_bars: Numeric;
};

export type BacktestMarket = {
  id: string;
  symbol: string;
  timeframe: string;
  row_count: number;
  start_time: string | null;
  end_time: string | null;
};

export type BacktestError = {
  id: number;
  symbol: string;
  timeframe: string;
  message: string;
};

export type BacktestRun = {
  id: string;
  name: string;
  status: "PENDING" | "QUEUED" | "RUNNING" | "COMPLETED" | "COMPLETED_WITH_ERRORS" | "FAILED" | "CANCELLED";
  progress: number;
  total_combinations: number;
  completed_combinations: number;
  signal_count: number;
  order_count: number;
  cancel_requested: boolean;
  error_message: string | null;
  request: BacktestRequest;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  summaries?: BacktestSummary[];
  markets?: BacktestMarket[];
  errors?: BacktestError[];
};

export type BacktestOrder = {
  id: string;
  series_id: string;
  rule_key: string;
  rule_label: string;
  signal_key: string;
  symbol: string;
  timeframe: string;
  pattern: "head_shoulders_top" | "inverse_head_shoulders";
  alert_type: string;
  direction: "LONG" | "SHORT";
  score: number;
  quantity: number;
  status: "INVALID" | "INCOMPLETE" | "CLOSED";
  exit_reason: "TAKE_PROFIT" | "STOP_LOSS" | "TIME_EXIT" | "SESSION_EXIT" | null;
  entry_time: string | null;
  exit_time: string | null;
  entry_price: Numeric | null;
  stop_price: Numeric | null;
  target_price: Numeric | null;
  exit_price: Numeric | null;
  gross_pnl: Numeric | null;
  net_pnl: Numeric | null;
  fees: Numeric | null;
  slippage: Numeric | null;
  r_multiple: Numeric | null;
  holding_bars: number;
  mfe_r: Numeric | null;
  mae_r: Numeric | null;
  cost_available: boolean;
  signal: Record<string, unknown>;
};

export type BacktestOrdersResponse = {
  items: BacktestOrder[];
  total: number;
  page: number;
  page_size: number;
  totals: {
    net_pnl: Numeric;
    fees: Numeric;
  };
};

export type BacktestEquityCurve = {
  rule_key: string;
  items: Array<{
    entry_time: string | null;
    time: string;
    net_pnl: Numeric;
    cumulative_net_pnl: Numeric;
  }>;
};

export type BacktestSeries = {
  symbol: string;
  timeframe: string;
  chart: {
    candles: Array<{
      index: number;
      time: string;
      display_time?: string;
      open: number;
      high: number;
      low: number;
      close: number;
      volume: number;
      ma?: Record<string, number | null>;
    }>;
    pivots: Array<{ index: number; time: string; price: number; kind: "high" | "low" }>;
    necklines: Array<{ from_index: number; to_index: number; from_price: number; to_price: number; confirmed: boolean }>;
  };
  signals: Array<Record<string, unknown>>;
};
