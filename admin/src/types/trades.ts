export interface Trade {
  id: number;
  timestamp: string;
  coin: string;
  side: "buy" | "sell";
  price: number;
  amount: number;
  total_krw: number;
  fee_krw: number;
  strategy: string;
  trigger_reason: string;
  trigger_value: number;
  param_k_value: number;
  param_stop_loss: number;
  param_trailing_stop: number;
  market_state_at_trade: string;
  btc_price_at_trade: number;
  rsi_at_trade: number;
  buy_trade_id: number | null;
  profit_pct: number | null;
  profit_krw: number | null;
  hold_duration_minutes: number | null;
  strategy_params_json: string;
  strategy_selection_reason: string;
  signal_confidence: number | null;
}

export interface TradesResponse {
  items: Trade[];
  total: number;
  page: number;
  limit: number;
  pages: number;
}

export interface TradeStats {
  period_days: number;
  total_trades: number;
  buys: number;
  sells: number;
  wins: number;
  losses: number;
  win_rate: number;
  avg_profit_pct: number;
  total_profit_krw: number;
  total_fees: number;
}

export interface DailyReturn {
  date: string;
  daily_pnl_pct: number;
  daily_pnl_krw: number;
  daily_return_pct: number;
  trade_count: number;
  total_trades: number;
  win_rate: number;
}

export interface TradeFiltersState {
  coin: string;
  side: string;
  strategy: string;
  market: string;  // #266 시장 필터 — '' (전체) / 'upbit' / 'kis_kr' / 'kis_us'
  date_from: string;
  date_to: string;
}
