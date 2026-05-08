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
  pattern: "head_shoulders_top" | "head_shoulders_range_top" | "inverse_head_shoulders";
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
