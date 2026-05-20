export type PivotPoint = {
  index: number;
  time: string;
  price: number;
  kind: "high" | "low";
};

export type Candle = {
  index: number;
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  ma?: Record<string, number | null>;
};

export type Neckline = {
  from_index: number;
  to_index: number;
  from_price: number;
  to_price: number;
  confirmed: boolean;
};

export type Signal = {
  symbol: string;
  timeframe: string;
  pattern: "head_shoulders_top" | "inverse_head_shoulders";
  alert_type: "right_shoulder_confirmed" | "neckline_break" | "right_shoulder_retest";
  left_shoulder: PivotPoint;
  left_neck: PivotPoint;
  head: PivotPoint;
  right_neck: PivotPoint;
  right_shoulder: PivotPoint;
  neckline_price: number;
  confirmed: boolean;
  score: number;
  reasons: string[];
  break_time: string | null;
  break_price: number | null;
  retest_time: string | null;
  retest_price: number | null;
  message: string;
};

export type ScanResponse = {
  symbol: string;
  timeframe: string;
  rows: number;
  start_time: string | null;
  end_time: string | null;
  config: Record<string, unknown>;
  signals: Signal[];
  chart: {
    candles: Candle[];
    pivots: PivotPoint[];
    necklines: Neckline[];
  };
};

export type SimulationStartResponse = {
  session_id: string;
  symbol: string;
  timeframe: string;
  total_rows: number;
  start_time: string;
  end_time: string;
};

export type SimulationStepResponse = {
  session_id: string;
  cursor: number;
  total_rows: number;
  done: boolean;
  latest_bar: Candle | null;
  scan: ScanResponse;
};

export type MarketSettings = {
  provider: string;
  base_url: string | null;
  api_key_set: string;
  market_module: string;
};

export type MarketSymbol = {
  symbol: string;
  name_cn: string | null;
  name_hk: string | null;
  name_en: string | null;
};

export type MarketSymbolsResponse = {
  symbol_type: string;
  symbols: MarketSymbol[];
};

export type WatchPoolItem = {
  id: string;
  name: string;
  symbol: string;
  timeframe: string;
  enabled: boolean;
  monitor_minutes: number;
  trading_sessions: string;
  min_head_to_neck_height: number;
  monitor_started_at: string | null;
  created_at: string | null;
  updated_at: string | null;
};

export type WatchPoolPayload = {
  name: string;
  symbol: string;
  timeframe: string;
  enabled: boolean;
  monitor_minutes: number;
  trading_sessions: string;
  min_head_to_neck_height: number;
};

export type HeadShouldersAlert = {
  id: string;
  watch_pool_id: string;
  symbol: string;
  timeframe: string;
  pattern: Signal["pattern"];
  alert_type: Signal["alert_type"];
  score: number;
  message: string;
  unique_key: string;
  signal_payload: Signal;
  chart_payload: ScanResponse["chart"];
  created_at: string | null;
};

export type HeadShouldersAlertSummary = Omit<HeadShouldersAlert, "chart_payload">;

export type AlertFeedback = {
  id: string;
  alert_id: string;
  symbol: string;
  timeframe: string;
  pattern: Signal["pattern"];
  alert_type: Signal["alert_type"];
  score: number;
  message: string;
  unique_key: string;
  signal_payload: Signal;
  chart_payload: ScanResponse["chart"];
  feedback_note: string;
  alert_created_at: string | null;
  created_at: string | null;
  updated_at: string | null;
};
