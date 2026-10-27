// Shapes returned by the backend API (only the fields the UI reads).

export interface CandleItem {
  timestamp: string;
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  is_closed: boolean;
}

export interface RiskStatusSummary {
  circuit_breaker_state: string;
  daily_loss_pct: number;
  max_daily_loss_pct: number;
  current_drawdown_pct: number;
  max_drawdown_pct: number;
  open_positions_count: number;
  max_open_positions: number;
  current_equity_usd: number;
  gross_exposure_usd: number;
  max_portfolio_exposure_pct: number;
  kill_switch_active: boolean;
}

export interface ShadowCandidateData {
  symbol: string;
  status: string;
  rank?: number | null;
  quoted_spread_pct?: number | null;
  observed_movement_less_spread_pct?: number | null;
  regime_status?: string;
  trend_regime?: string | null;
  volatility_regime?: string | null;
}

export interface ShadowScanData {
  scan_id: string;
  timeframe: string;
  scanned_at: string;
  candidates: ShadowCandidateData[];
}

export interface OpportunityAssessmentData {
  decision: string;
  selected_symbol?: string | null;
  selection_reason: string;
  evidence: {
    matured_evaluations?: number;
    mean_top_minus_alternatives_net_pct?: number | null;
    interpretation?: string;
  };
  candidates: Array<{
    symbol: string;
    cost_adjusted_movement_pct?: number | null;
    eligible: boolean;
  }>;
}

export interface ResearchContextData {
  notes: Array<{
    id: string;
    kind: string;
    title: string;
    statement: string;
    validation_status: string;
    timeframe?: string | null;
  }>;
  disclaimer: string;
}

export interface LLMUsageBucket {
  requests: number;
  failures: number;
  total_tokens: number;
  estimated_cost_usd: number | null;
}

export interface LLMStatusData {
  mode: string;
  scan_mode?: string;
  primary_provider?: string | null;
  configured_providers: string[];
  fallback_providers?: string[];
  usage?: {
    requests: number;
    request_attempts?: number;
    tokens_used: number;
    daily_token_budget: number;
    budget_exhausted: boolean;
    daily_request_budget?: number;
    request_budget_exhausted?: boolean;
    cycle_token_budget?: number;
    cycle_tokens_used?: number;
    cycle_budget_exhausted?: boolean;
    cycle_request_budget?: number;
    cycle_requests_used?: number;
    cycle_request_budget_exhausted?: boolean;
    fallbacks: number;
    provider_cooldowns?: Array<{ provider: string; reason: string; seconds_remaining: number }>;
    estimated_cost_usd: number | null;
    pricing: { configured: boolean };
    by_agent: Record<string, LLMUsageBucket>;
    by_provider: Record<string, LLMUsageBucket>;
  };
}

export type LLMScanModeValue = 'savings' | 'normal' | 'high';

export interface LLMScanModePresetData {
  mode: LLMScanModeValue;
  label: string;
  description: string;
  max_markets: number;
  parallel: boolean;
  llm_cycle_request_budget: number;
  llm_cycle_token_budget: number;
}

export interface LLMScanModeData {
  mode: LLMScanModeValue;
  active: LLMScanModePresetData;
  presets: LLMScanModePresetData[];
}

export interface DriftWindowMetrics {
  sample_size: number;
  win_rate_pct: number | null;
  avg_net_pnl_pct: number | null;
  profit_factor: number | null;
}

export interface DriftReportData {
  status: 'insufficient_data' | 'stable' | 'watch' | 'degraded';
  reasons: string[];
  recent: DriftWindowMetrics;
  baseline: DriftWindowMetrics;
  disclaimer: string;
}

export interface MarketRegimeData {
  status: 'insufficient_data' | 'classified';
  trend: 'uptrend' | 'downtrend' | 'range' | null;
  volatility: 'low' | 'normal' | 'high' | null;
  metrics: {
    sample_size: number;
    data_stale: boolean | null;
    ema_separation_pct: number | null;
    ema_slope_pct: number | null;
    atr_pct: number | null;
    atr_percentile: number | null;
    trend_strength: number | null;
  };
  reasons: string[];
  disclaimer: string;
}

export interface ConcentrationReportData {
  timeframe: string;
  equity_usd: number;
  gross_exposure_usd: number;
  gross_exposure_pct: number | null;
  max_position_size_pct: number;
  positions: Array<{
    symbol: string;
    exposure_usd: number;
    equity_pct: number | null;
    limit_usd: number | null;
    within_limit: boolean | null;
  }>;
  correlations: Array<{
    left: string;
    right: string;
    correlation: number | null;
    sample_size: number;
    high: boolean;
  }>;
  warnings: string[];
  disclaimer: string;
}

export interface CoordinatorDecisionData {
  final_signal: string;
  aggregate_confidence: number;
  action_recommendation: string;
  consensus_level: number;
  recommended_stop?: number;
  recommended_target?: number;
  reasoning_summary: string;
  agent_weights?: Record<string, number>;
}

export interface EncryptedCredentialData {
  exchange: string;
  masked_key: string;
  is_testnet: boolean;
  has_withdrawal_permission: boolean;
  permissions_verified_at: string | null;
  ip_restricted: boolean | null;
}

export interface ConnectionPingResult {
  exchange: string;
  is_testnet: boolean;
  latency_ms: number;
  balances: Record<string, number>;
  permissions_checked: boolean;
  can_trade: boolean | null;
  withdrawals_enabled: boolean | null;
  ip_restricted: boolean | null;
  warnings: string[];
}

export interface RealTradingStatusData {
  enabled: boolean;
  exchange: string;
  testnet: boolean;
  credentials_configured: boolean;
  allowed_symbols: string[];
  max_notional: number | string;
  require_manual_confirmation: boolean;
  autonomous_real_enabled: boolean;
  open_positions: number;
  guard_running: boolean;
  protection_mode?: string;
  oco_enabled?: boolean;
  oco_protected_positions?: number;
  oco_discovery_failed_exchanges?: string[];
}

export type TradingEnvironment = 'testnet' | 'mainnet';

export interface RuntimeTradingConfigData {
  execution_mode: 'paper' | 'real' | 'backtest';
  trading_environment: TradingEnvironment;
  enable_real_trading: boolean;
  allow_mainnet_trading: boolean;
  require_manual_confirmation: boolean;
  real_trading_max_notional: number | string;
  real_trading_allowed_symbols: string[];
  real_trading_oco_enabled: boolean;
  enable_autonomous_real_trading: boolean;
  real_trading_active: boolean;
  credentials: Record<string, boolean>;
}

export interface AlertStatusData {
  enabled: boolean;
  channels: Array<{ name: string; configured: boolean }>;
  sent_count: number;
  failed_count: number;
}

export interface AutonomousLogEntry {
  id: string;
  timestamp: string;
  level: string;
  symbol: string;
  message: string;
  key?: string | null;
  params?: Record<string, unknown>;
  price?: number | null;
}

export interface AutonomousBotConfig {
  symbol: string;
  scan_symbols?: string[];
  timeframe: string;
  interval_seconds: number;
  llm_analysis_interval_seconds?: number;
  execution_mode: string;
  confirm_real_trading: boolean;
  min_confidence_pct: number;
  risk_per_trade_pct: number;
  max_open_positions: number;
  auto_execute: boolean;
  stop_loss_pct: number;
  take_profit_pct: number;
}

export interface AutonomousBotStatus {
  is_running: boolean;
  config: AutonomousBotConfig;
  total_cycles: number;
  trades_executed: number;
  uptime_seconds: number;
  last_cycle_at?: string | null;
  last_analysis_at?: string | null;
  last_price?: number | null;
  last_symbol?: string | null;
  last_signal?: string | null;
  last_confidence?: number | null;
  last_error?: string | null;
  active_positions_count: number;
  logs: AutonomousLogEntry[];
}

export interface AgentPerformanceData {
  agent_type: string;
  decisions_scored: number;
  correct: number;
  abstained: number;
  hit_rate_pct: number;
  avg_confidence: number;
  avg_processing_time_ms: number;
}

export interface MlopsPerformanceData {
  total_decisions: number;
  executed: number;
  resolved: number;
  win_rate_pct: number;
  net_pnl: number | string;
  profit_factor: number | null;
  avg_confidence: number;
  by_agent: AgentPerformanceData[];
  by_signal: Array<{
    signal: string;
    decisions: number;
    resolved: number;
    correct: number;
    hit_rate_pct: number;
    net_pnl: number | string;
  }>;
  by_symbol: Array<{
    symbol: string;
    decisions: number;
    resolved: number;
    win_rate_pct: number;
    net_pnl: number | string;
  }>;
  confidence_buckets: Array<{
    lower: number;
    upper: number;
    decisions: number;
    correct: number;
    hit_rate_pct: number;
    minimum_sample_size: number;
    sample_sufficient: boolean;
  }>;
  unattributed_orders: number;
  data_available: boolean;
}

export interface AdaptiveWeightsReport {
  enabled: boolean;
  applied: boolean;
  min_samples: number;
  weights: Record<string, number>;
  agents: Array<{
    agent_type: string;
    default_weight: number;
    weight: number;
    multiplier: number;
    decisions_scored: number;
    hit_rate_pct: number | null;
    adjusted: boolean;
    reason: string;
  }>;
  note: string;
}

export interface PositionData {
  id: string;
  symbol: string;
  mode: string;
  side: 'long' | 'short';
  entry_price: number | string;
  quantity: number | string;
  current_price: number | string;
  stop_loss: number | string | null;
  take_profit: number | string | null;
  unrealized_pnl: number | string;
  unrealized_pnl_pct: number | string;
  opened_at: string;
}

export interface OrderData {
  id: string;
  symbol: string;
  mode: string;
  side: 'buy' | 'sell';
  order_type: string;
  status: string;
  quantity: number | string;
  filled_quantity: number | string;
  average_fill_price: number | string | null;
  coordinator_decision_id: string | null;
  created_at: string;
  rejection_reason: string | null;
}

export interface TradeOutcomeData {
  decision_id: string;
  symbol: string;
  mode: string | null;
  status: 'closed' | 'open' | 'unexecuted';
  outcome: 'win' | 'loss' | 'breakeven' | 'open';
  entry_price: number | string | null;
  exit_price: number | string | null;
  quantity: number | string;
  net_pnl: number | string;
  net_pnl_pct: number;
  fees: number | string;
  opened_at: string | null;
  closed_at: string | null;
}

export const AVAILABLE_SYMBOLS = [
  { id: 'BTC/USDT', name: 'Bitcoin', ticker: 'BTC' },
  { id: 'ETH/USDT', name: 'Ethereum', ticker: 'ETH' },
  { id: 'SOL/USDT', name: 'Solana', ticker: 'SOL' },
  { id: 'BNB/USDT', name: 'BNB', ticker: 'BNB' },
] as const;

export const TIMEFRAMES = [
  { id: '15m', label: '15 min' },
  { id: '1h', label: '1 h' },
  { id: '4h', label: '4 h' },
  { id: '1d', label: '1 día' },
] as const;

export const AGENT_TYPES = ['technical_analyst', 'price_action_analyst', 'risk_manager'] as const;
