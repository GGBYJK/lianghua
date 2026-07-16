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
  fee_open_rate: Numeric;
  fee_close_rate: Numeric;
  fee_open_fixed: Numeric;
  fee_close_fixed: Numeric;
  enabled: boolean;
};

export type MarketQuote = {
  symbol: string;
  last_price: Numeric;
  source: string;
  market_time: string | null;
  updated_at: string | null;
};
