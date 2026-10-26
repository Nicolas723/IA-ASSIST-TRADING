import { useState, useEffect } from 'react';
import './App.css';
import toast, { Toaster } from 'react-hot-toast';
import { MarketChart } from './components/MarketChart';
import type { CandleItem } from './components/MarketChart';

// Formateadores seguros para evitar fallos si algún dato no llega del backend
const safeNum = (v: unknown, fallback = 0): number =>
  typeof v === 'number' && !isNaN(v) ? v : fallback;
const safeFix = (v: unknown, dec = 2): string => safeNum(v).toFixed(dec);
const safeLoc = (v: unknown, opts?: Intl.NumberFormatOptions): string =>
  safeNum(v).toLocaleString('es-ES', opts);
const safeUsd = (v: unknown): string => {
  if (typeof v !== 'number' || isNaN(v)) return 'n/d';
  return `$${v < 0.01 ? v.toFixed(6) : v.toFixed(4)}`;
};
const safeMetric = (v: unknown, dec = 2): string => (v == null ? 'n/d' : safeFix(v, dec));

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
  reject_reasons?: string[];
}

interface ShadowCandidateData {
  symbol: string;
  status: string;
  rank?: number | null;
  last_price?: number | null;
  lookback_return_pct?: number | null;
  quoted_spread_pct?: number | null;
  observed_movement_less_spread_pct?: number | null;
  spread_status?: string;
  regime_status?: string;
  trend_regime?: string | null;
  volatility_regime?: string | null;
  error?: string | null;
}

interface ShadowScanData {
  scan_id: string;
  timeframe: string;
  scanned_at: string;
  candidates: ShadowCandidateData[];
  execution_enabled: boolean;
}

interface OpportunityCandidateData {
  symbol: string;
  rank?: number | null;
  cost_adjusted_movement_pct?: number | null;
  eligible: boolean;
  abstain_reasons: string[];
}

interface OpportunityAssessmentData {
  decision: string;
  selected_symbol?: string | null;
  selection_reason: string;
  execution_enabled: boolean;
  policy: Record<string, number>;
  evidence: {
    matured_evaluations?: number;
    mean_top_minus_alternatives_net_pct?: number | null;
    net_evidence_positive?: boolean;
    interpretation?: string;
  };
  candidates: OpportunityCandidateData[];
}

interface ResearchNoteData {
  id: string;
  kind: string;
  title: string;
  statement: string;
  validation_status: string;
  symbols: string[];
  timeframe?: string | null;
  created_at: string;
}

interface ResearchContextData {
  symbol?: string | null;
  timeframe?: string | null;
  notes: ResearchNoteData[];
  usage_policy: string;
  disclaimer: string;
}

interface LLMUsageBucket {
  requests: number;
  failures: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  avg_tokens_per_request: number | null;
  avg_latency_ms: number | null;
  estimated_cost_usd: number | null;
  models: string[];
}

interface LLMStatusData {  mode: string;
  primary_provider?: string | null;
  configured_providers: string[];
  fallback_providers?: string[];
  usage?: {
    requests: number;
    request_attempts?: number;
    tokens_used: number;
    prompt_tokens: number;
    completion_tokens: number;
    avg_tokens_per_request: number | null;
    daily_token_budget: number;
    tokens_remaining: number | null;
    budget_exhausted: boolean;
    daily_request_budget?: number;
    requests_remaining?: number | null;
    request_budget_exhausted?: boolean;
    cycle_token_budget?: number;
    cycle_tokens_used?: number;
    cycle_tokens_remaining?: number | null;
    cycle_budget_exhausted?: boolean;
    cycle_request_budget?: number;
    cycle_requests_used?: number;
    cycle_requests_remaining?: number | null;
    cycle_request_budget_exhausted?: boolean;
    fallbacks: number;
    provider_cooldowns?: Array<{
      provider: string;
      reason: string;
      seconds_remaining: number;
      until_utc: string;
    }>;
    estimated_cost_usd: number | null;
    pricing: {
      input_usd_per_million_tokens: number;
      output_usd_per_million_tokens: number;
      configured: boolean;
    };
    by_agent: Record<string, LLMUsageBucket>;
    by_provider: Record<string, LLMUsageBucket>;
    cost_estimate_note: string;
    date_utc: string;
  };
}

interface DriftWindowMetrics {
  label: string;
  sample_size: number;
  win_rate_pct: number | null;
  avg_net_pnl_pct: number | null;
  total_net_pnl: number | string;
  profit_factor: number | null;
}

interface DriftSignalData {
  metric: string;
  recent: number | null;
  baseline: number | null;
  delta: number | null;
  threshold: number;
  breached: boolean;
}

interface DriftReportData {
  generated_at: string;
  mode: string | null;
  symbol: string | null;
  status: 'insufficient_data' | 'stable' | 'watch' | 'degraded';
  reasons: string[];
  recent: DriftWindowMetrics;
  baseline: DriftWindowMetrics;
  signals: DriftSignalData[];
  enforcement: string;
  disclaimer: string;
}

interface MarketRegimeMetricsData {
  sample_size: number;
  active_candles_excluded: number;
  last_close: number | null;
  last_candle_at: string | null;
  data_age_seconds: number | null;
  data_stale: boolean | null;
  ema_fast: number | null;
  ema_slow: number | null;
  ema_separation_pct: number | null;
  ema_slope_pct: number | null;
  atr_pct: number | null;
  atr_percentile: number | null;
  trend_strength: number | null;
  lookback_high: number | null;
  lookback_low: number | null;
  range_position_pct: number | null;
}

interface MarketRegimeData {
  generated_at: string;
  symbol: string;
  timeframe: string;
  status: 'insufficient_data' | 'classified';
  trend: 'uptrend' | 'downtrend' | 'range' | null;
  volatility: 'low' | 'normal' | 'high' | null;
  metrics: MarketRegimeMetricsData;
  reasons: string[];
  descriptive_only: boolean;
  affects_trading: boolean;
  execution_enabled: boolean;
  disclaimer: string;
}

interface ConcentrationSymbolExposure {
  symbol: string;
  exposure_usd: number;
  equity_pct: number | null;
  limit_usd: number | null;
  headroom_usd: number | null;
  within_limit: boolean | null;
}

interface ConcentrationCorrelationPair {
  left: string;
  right: string;
  correlation: number | null;
  sample_size: number;
  high: boolean;
}

interface ConcentrationReportData {
  generated_at: string;
  mode: string;
  timeframe: string;
  lookback: number;
  universe: string[];
  equity_usd: number;
  gross_exposure_usd: number;
  gross_exposure_pct: number | null;
  max_portfolio_exposure_pct: number;
  max_position_size_pct: number;
  positions: ConcentrationSymbolExposure[];
  correlations: ConcentrationCorrelationPair[];
  high_correlation_threshold: number;
  warnings: string[];
  read_only: boolean;
  affects_trading: boolean;
  disclaimer: string;
}

interface AgentVoteData {
  agent_type: string;
  signal: string;
  confidence: number;
  reasoning: string;
}

interface CoordinatorDecisionData {
  final_signal: string;
  aggregate_confidence: number;
  action_recommendation: string;
  consensus_level: number;
  recommended_entry?: number;
  recommended_stop?: number;
  recommended_target?: number;
  reasoning_summary: string;
  agent_votes?: AgentVoteData[];
}

interface EncryptedCredentialData {
  exchange: string;
  masked_key: string;
  is_testnet: boolean;
  can_trade: boolean;
  status: string;
  last_verified_at: string | null;
}

interface ConnectionPingResult {
  exchange: string;
  status: string;
  is_testnet: boolean;
  latency_ms: number;
  balances: Record<string, number>;
}

interface RealTradingStatusData {
  enabled: boolean;
  execution_mode: string;
  exchange: string;
  testnet: boolean;
  mainnet_allowed: boolean;
  credentials_configured: boolean;
  allowed_symbols: string[];
  max_notional: number | string;
  require_manual_confirmation: boolean;
  autonomous_real_enabled: boolean;
  open_positions: number;
  guard_running: boolean;
  protection_mode?: string;
  reconciliation_interval_seconds?: number;
  oco_enabled?: boolean;
  oco_protected_positions?: number;
  oco_discovery_failed_exchanges?: string[];
}

interface RuntimeTradingConfigData {
  execution_mode: 'paper' | 'real' | 'backtest';
  trading_environment: 'testnet' | 'mainnet';
  enable_real_trading: boolean;
  allow_mainnet_trading: boolean;
  require_manual_confirmation: boolean;
  real_trading_max_notional: number | string;
  real_trading_allowed_symbols: string[];
  real_trading_oco_enabled: boolean;
  enable_autonomous_real_trading: boolean;
  real_trading_active: boolean;
  mainnet_trading_active: boolean;
  credentials: Record<string, boolean>;
  runtime_override_active: boolean;
}

interface AlertChannelStatusData {
  name: string;
  configured: boolean;
}

interface AlertStatusData {
  enabled: boolean;
  min_severity: string;
  channels: AlertChannelStatusData[];
  sent_count: number;
  failed_count: number;
  last_sent_at: string | null;
  last_error: string | null;
}

interface AutonomousLogEntry {
  id: string;
  timestamp: string;
  level: string;
  cycle: number;
  symbol: string;
  message: string;
  signal?: string | null;
  confidence_pct?: number | null;
  price?: number | null;
  order_id?: string | null;
}

interface AutonomousBotConfig {
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

interface AutonomousBotStatus {
  is_running: boolean;
  config: AutonomousBotConfig;
  total_cycles: number;
  trades_executed: number;
  started_at?: string | null;
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

interface AgentPerformanceData {
  agent_type: string;
  decisions_scored: number;
  correct: number;
  incorrect: number;
  abstained: number;
  hit_rate_pct: number;
  avg_confidence: number;
  avg_processing_time_ms: number;
}

interface SignalPerformanceData {
  signal: string;
  decisions: number;
  resolved: number;
  correct: number;
  hit_rate_pct: number;
  net_pnl: number | string;
}

interface SymbolPerformanceData {
  symbol: string;
  decisions: number;
  resolved: number;
  win_rate_pct: number;
  net_pnl: number | string;
}

interface ConfidenceBucketData {
  lower: number;
  upper: number;
  decisions: number;
  correct: number;
  hit_rate_pct: number;
  minimum_sample_size: number;
  sample_sufficient: boolean;
}

interface MlopsPerformanceData {
  total_decisions: number;
  executed: number;
  resolved: number;
  open: number;
  unexecuted: number;
  win_rate_pct: number;
  net_pnl: number | string;
  gross_pnl: number | string;
  fees: number | string;
  profit_factor: number | null;
  avg_pnl: number | string;
  avg_confidence: number;
  by_agent: AgentPerformanceData[];
  by_signal: SignalPerformanceData[];
  by_symbol: SymbolPerformanceData[];
  confidence_buckets: ConfidenceBucketData[];
  unattributed_orders: number;
  generated_at: string;
  data_available: boolean;
}

const AVAILABLE_SYMBOLS = [
  { id: 'BTC/USDT', name: 'Bitcoin (BTC)', icon: '₿' },
  { id: 'ETH/USDT', name: 'Ethereum (ETH)', icon: 'Ξ' },
  { id: 'SOL/USDT', name: 'Solana (SOL)', icon: '◎' },
  { id: 'BNB/USDT', name: 'Binance Coin (BNB)', icon: '🔶' },
];

const TIMEFRAMES = [
  { id: '15m', label: '15 Minutos' },
  { id: '1h', label: '1 Hora' },
  { id: '4h', label: '4 Horas' },
  { id: '1d', label: '1 Día' },
];

const AGENT_LABELS: Record<string, string> = {
  technical_analyst: 'Analista Técnico',
  price_action_analyst: 'Acción del Precio',
  sentiment_analyst: 'Sentimiento',
  macro_analyst: 'Macro',
  fundamental_analyst: 'Fundamental',
  strategy_optimizer: 'Optimizador',
  system_supervisor: 'Supervisor',
  performance_coach: 'Coach de Rendimiento',
};

const SIGNAL_LABELS: Record<string, string> = {
  strong_buy: 'Compra fuerte',
  buy: 'Compra',
  neutral: 'Neutral',
  sell: 'Venta',
  strong_sell: 'Venta fuerte',
  hold: 'Mantener',
  no_signal: 'Sin señal',
};

type DashboardTab = 'bot' | 'agents' | 'mlops' | 'settings';

export default function App() {
  const [activeTab, setActiveTab] = useState<DashboardTab>('bot');
  const [status, setStatus] = useState<Record<string, string>>({});

  // Mercado y WebSockets
  const [selectedSymbol, setSelectedSymbol] = useState<string>('BTC/USDT');
  const [selectedTimeframe, setSelectedTimeframe] = useState<string>('1h');
  const [candles, setCandles] = useState<CandleItem[]>([]);
  const [isLoadingCandles, setIsLoadingCandles] = useState<boolean>(false);
  const [wsConnected, setWsConnected] = useState<boolean>(false);
  const [livePrice, setLivePrice] = useState<number | null>(null);

  // Cartera y Operaciones

  // Bot Autónomo
  const [autonomousStatus, setAutonomousStatus] = useState<AutonomousBotStatus | null>(null);
  const [botInterval, setBotInterval] = useState<number>(60);
  const [botExecutionMode, setBotExecutionMode] = useState<'PAPER' | 'REAL'>('PAPER');
  const [botMinConfidence, setBotMinConfidence] = useState<number>(60);
  const [botMaxOpenPositions, setBotMaxOpenPositions] = useState<number>(1);
  const [botRiskLevel, setBotRiskLevel] = useState<'conservative' | 'moderate' | 'aggressive'>('conservative');
  const [botAutoExecute, setBotAutoExecute] = useState<boolean>(true);
  const [isTogglingBot, setIsTogglingBot] = useState<boolean>(false);

  // Análisis Multi-Agente
  const [aiDecision, setAiDecision] = useState<CoordinatorDecisionData | null>(null);
  const [isAnalyzingAI, setIsAnalyzingAI] = useState<boolean>(false);

  // MLOps / aprendizaje continuo
  const [mlopsPerformance, setMlopsPerformance] = useState<MlopsPerformanceData | null>(null);
  const [isLoadingMlops, setIsLoadingMlops] = useState<boolean>(false);
  const [mlopsMode, setMlopsMode] = useState<'all' | 'paper' | 'real'>('all');
  const [mlopsSymbol, setMlopsSymbol] = useState<string>('');

  // Riesgo y Seguridad
  const [riskStatus, setRiskStatus] = useState<RiskStatusSummary | null>(null);
  const [driftReport, setDriftReport] = useState<DriftReportData | null>(null);
  const [regimeReport, setRegimeReport] = useState<MarketRegimeData | null>(null);
  const [concentrationReport, setConcentrationReport] = useState<ConcentrationReportData | null>(null);
  const [isLoadingRegime, setIsLoadingRegime] = useState<boolean>(false);

  // Oportunidades en sombra y coste de IA (solo lectura)
  const [latestShadowScan, setLatestShadowScan] = useState<ShadowScanData | null>(null);
  const [opportunityAssessment, setOpportunityAssessment] = useState<OpportunityAssessmentData | null>(null);
  const [llmStatus, setLlmStatus] = useState<LLMStatusData | null>(null);
  const [researchContext, setResearchContext] = useState<ResearchContextData | null>(null);

  // Credenciales
  const [vaultCredentials, setVaultCredentials] = useState<EncryptedCredentialData[]>([]);
  const [vaultApiKey, setVaultApiKey] = useState<string>('');
  const [vaultApiSecret, setVaultApiSecret] = useState<string>('');
  const [vaultIsTestnet, setVaultIsTestnet] = useState<boolean>(true);
  const [isSavingVault, setIsSavingVault] = useState<boolean>(false);
  const [pingResult, setPingResult] = useState<ConnectionPingResult | null>(null);
  const [isTestingConn, setIsTestingConn] = useState<boolean>(false);

  // Trading real y alertas
  const [realTradingStatus, setRealTradingStatus] = useState<RealTradingStatusData | null>(null);
  const [runtimeTradingConfig, setRuntimeTradingConfig] = useState<RuntimeTradingConfigData | null>(null);
  const [isSavingRuntimeConfig, setIsSavingRuntimeConfig] = useState<boolean>(false);
  const [runtimeMaxNotional, setRuntimeMaxNotional] = useState<string>('50');
  const [runtimeAllowedSymbols, setRuntimeAllowedSymbols] = useState<string>('BTC/USDT');
  const [runtimeAutonomousReal, setRuntimeAutonomousReal] = useState<boolean>(false);
  const [runtimeOcoEnabled, setRuntimeOcoEnabled] = useState<boolean>(true);
  const [alertStatus, setAlertStatus] = useState<AlertStatusData | null>(null);
  const [isTestingAlert, setIsTestingAlert] = useState<boolean>(false);
  const [isReconcilingReal, setIsReconcilingReal] = useState<boolean>(false);

  // Refrescos periódicos
  const refreshAutonomousStatus = () => {
    fetch('/api/v1/autonomous/status')
      .then((res) => res.json())
      .then((data: AutonomousBotStatus) => {
        setAutonomousStatus(data);
        // While stopped, these controls are a local draft for the next session.
        // The polling endpoint keeps returning the engine's old config (often
        // "paper"), so syncing it here would overwrite a user's selection.
        if (data?.is_running && data.config) {
          setBotInterval(data.config.interval_seconds);
          setBotExecutionMode(data.config.execution_mode === 'real' ? 'REAL' : 'PAPER');
          setBotMinConfidence(data.config.min_confidence_pct);
          setBotMaxOpenPositions(data.config.max_open_positions);
          setBotAutoExecute(data.config.auto_execute);
        }
      })
      .catch((err) => console.error('Error estado autónomo', err));
  };

  const refreshVault = () => {
    fetch('/api/v1/vault/status')
      .then((res) => res.json())
      .then((data: EncryptedCredentialData[]) => setVaultCredentials(data || []))
      .catch((err) => console.error('Error estado de claves', err));
  };

  const refreshRisk = () => {
    fetch('/api/v1/risk/status')
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (data) setRiskStatus(data);
      })
      .catch((err) => console.error('Error riesgo', err));
  };

  const refreshDrift = () => {
    fetch('/api/v1/mlops/drift')
      .then((res) => (res.ok ? res.json() : null))
      .then((data: DriftReportData | null) => {
        if (data) setDriftReport(data);
      })
      .catch((err) => console.error('Error drift', err));
  };

  const refreshRegime = () => {
    const encoded = encodeURIComponent(selectedSymbol);
    const params = new URLSearchParams({ timeframe: selectedTimeframe, limit: '200' });
    setIsLoadingRegime(true);
    fetch(`/api/v1/market/${encoded}/regime?${params.toString()}`)
      .then((res) => (res.ok ? res.json() : null))
      .then((data: MarketRegimeData | null) => {
        if (data) setRegimeReport(data);
      })
      .catch((err) => console.error('Error régimen de mercado', err))
      .finally(() => setIsLoadingRegime(false));
  };

  const refreshConcentration = () => {
    const params = new URLSearchParams({ timeframe: '1h', lookback: '200' });
    fetch(`/api/v1/risk/concentration?${params.toString()}`)
      .then((res) => (res.ok ? res.json() : null))
      .then((data: ConcentrationReportData | null) => {
        if (data) setConcentrationReport(data);
      })
      .catch((err) => console.error('Error concentración', err));
  };

  const refreshOpportunities = () => {
    fetch('/api/v1/agents/scan/shadow/history?limit=1')
      .then((res) => (res.ok ? res.json() : []))
      .then((data: ShadowScanData[]) => {
        const latest = Array.isArray(data) && data.length > 0 ? data[0] : null;
        setLatestShadowScan(latest);
        if (!latest) {
          setOpportunityAssessment(null);
          return;
        }
        fetch(`/api/v1/agents/scan/shadow/${latest.scan_id}/assessment`, { method: 'POST' })
          .then((res) => (res.ok ? res.json() : null))
          .then((assessment: OpportunityAssessmentData | null) => setOpportunityAssessment(assessment))
          .catch((err) => console.error('Error evaluación de oportunidades', err));
      })
      .catch((err) => console.error('Error historial shadow', err));
  };

  const refreshLLMStatus = () => {
    fetch('/api/v1/agents/llm/status')
      .then((res) => (res.ok ? res.json() : null))
      .then((data: LLMStatusData | null) => setLlmStatus(data))
      .catch((err) => console.error('Error estado LLM', err));
  };

  const refreshResearchContext = () => {
    const params = new URLSearchParams({ symbol: selectedSymbol, limit: '5' });
    if (selectedTimeframe) params.set('timeframe', selectedTimeframe);
    fetch(`/api/v1/research/context?${params.toString()}`)
      .then((res) => (res.ok ? res.json() : null))
      .then((data: ResearchContextData | null) => setResearchContext(data))
      .catch((err) => console.error('Error contexto de investigación', err));
  };

  const refreshRealTrading = () => {
    fetch('/api/v1/trading/real/status')
      .then((res) => (res.ok ? res.json() : null))
      .then((data: RealTradingStatusData | null) => setRealTradingStatus(data))
      .catch((err) => console.error('Error estado trading real', err));
  };

  const refreshRuntimeConfig = () => {
    fetch('/api/v1/trading/runtime-config')
      .then(async (res) => {
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
        return data as RuntimeTradingConfigData;
      })
      .then((data) => {
        setRuntimeTradingConfig(data);
        setVaultIsTestnet(data.trading_environment === 'testnet');
        setRuntimeMaxNotional(String(data.real_trading_max_notional));
        setRuntimeAllowedSymbols(data.real_trading_allowed_symbols.join(', '));
        setRuntimeAutonomousReal(data.enable_autonomous_real_trading);
        setRuntimeOcoEnabled(data.real_trading_oco_enabled);
      })
      .catch((err) => console.error('Error configuración de trading', err));
  };

  const saveRuntimeConfig = async (
    changes: Partial<RuntimeTradingConfigData>,
    confirm = false,
  ) => {
    setIsSavingRuntimeConfig(true);
    try {
      const res = await fetch('/api/v1/trading/runtime-config', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...changes, confirm }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
      setRuntimeTradingConfig(data as RuntimeTradingConfigData);
      setVaultIsTestnet(data.trading_environment === 'testnet');
      setRuntimeMaxNotional(String(data.real_trading_max_notional));
      setRuntimeAllowedSymbols(data.real_trading_allowed_symbols.join(', '));
      setRuntimeAutonomousReal(data.enable_autonomous_real_trading);
      setRuntimeOcoEnabled(data.real_trading_oco_enabled);
      refreshRealTrading();
      toast.success('Configuración de ejecución guardada.');
      return true;
    } catch (err) {
      toast.error(`No se pudo guardar: ${(err as Error).message}`);
      return false;
    } finally {
      setIsSavingRuntimeConfig(false);
    }
  };

  const refreshAlerts = () => {
    fetch('/api/v1/alerts/status')
      .then((res) => (res.ok ? res.json() : null))
      .then((data: AlertStatusData | null) => setAlertStatus(data))
      .catch((err) => console.error('Error estado alertas', err));
  };

  const refreshMlopsPerformance = () => {
    setIsLoadingMlops(true);
    const params = new URLSearchParams({ limit: '200' });
    if (mlopsMode !== 'all') params.set('mode', mlopsMode);
    if (mlopsSymbol) params.set('symbol', mlopsSymbol);
    fetch(`/api/v1/mlops/performance?${params.toString()}`)
      .then(async (res) => {
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
        return data;
      })
      .then((data: MlopsPerformanceData) => setMlopsPerformance(data))
      .catch((err) => {
        console.error('Error métricas MLOps', err);
        toast.error(`No se pudieron cargar las métricas: ${err.message}`);
      })
      .finally(() => setIsLoadingMlops(false));
  };

  const handleTestAlert = () => {
    setIsTestingAlert(true);
    fetch('/api/v1/alerts/test', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ severity: 'info' }),
    })
      .then(async (res) => {
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
        return data;
      })
      .then((data) => {
        if (data.status) setAlertStatus(data.status);
        if (data.delivered) {
          toast.success('Alerta de prueba entregada correctamente.');
        } else {
          toast.error('No se pudo entregar. Revisa ALERTS_ENABLED y los canales configurados.');
        }
      })
      .catch((err) => toast.error(`Error al enviar alerta: ${err.message}`))
      .finally(() => setIsTestingAlert(false));
  };

  const handleReconcileReal = () => {
    setIsReconcilingReal(true);
    fetch('/api/v1/trading/real/reconcile', { method: 'POST' })
      .then(async (res) => {
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
        return data;
      })
      .then((data) => {
        refreshRealTrading();
        const removed = Array.isArray(data.removed) ? data.removed : [];
        if (removed.length > 0) {
          toast.success(`Reconciliado. Posiciones cerradas fuera: ${removed.join(', ')}`);
        } else {
          toast.success('Posiciones reales sincronizadas con el exchange.');
        }
      })
      .catch((err) => toast.error(`Error al reconciliar: ${err.message}`))
      .finally(() => setIsReconcilingReal(false));
  };

  // Carga inicial
  useEffect(() => {
    fetch('/api/v1/status')
      .then((res) => res.json())
      .then((data) => setStatus(data))
      .catch((err) => console.error('Error status', err));

    refreshAutonomousStatus();
    refreshVault();
    refreshRisk();
    refreshDrift();
    refreshConcentration();
    refreshOpportunities();
    refreshLLMStatus();
    refreshResearchContext();
    refreshRealTrading();
    refreshRuntimeConfig();
    refreshAlerts();

    const intervalTimer = setInterval(() => {
      refreshAutonomousStatus();
    }, 3000);

    return () => clearInterval(intervalTimer);
  }, []);

  // Métricas de aprendizaje: se cargan al abrir la pestaña y se refrescan cada 15 s.
  useEffect(() => {
    if (activeTab !== 'mlops') return;
    refreshMlopsPerformance();
    const timer = setInterval(refreshMlopsPerformance, 15000);
    return () => clearInterval(timer);
  }, [activeTab, mlopsMode, mlopsSymbol]);

  // Conexión WebSocket para precio en tiempo real
  useEffect(() => {
    let ws: WebSocket | null = null;
    let pingInterval: ReturnType<typeof setInterval> | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let retryCount = 0;
    let isUnmounted = false;

    const connectWs = () => {
      if (isUnmounted) return;
      const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const wsSymbol = selectedSymbol.replace('/', '-');
      const wsUrl = `${wsProtocol}//${window.location.host}/api/v1/ws/market/${wsSymbol}`;

      try {
        ws = new WebSocket(wsUrl);
      } catch (err) {
        console.error('Error de inicialización WebSocket', err);
        return;
      }

      ws.onopen = () => {
        setWsConnected(true);
        retryCount = 0;
        pingInterval = setInterval(() => {
          if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ action: 'ping' }));
          }
        }, 15000);
      };

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          if (data.type === 'subscribed') {
            setWsConnected(true);
          } else if (data.type === 'price_tick' && typeof data.price === 'number') {
            setLivePrice(data.price);
            setCandles((prev) => {
              if (!prev || prev.length === 0) return prev;
              const last = prev[prev.length - 1];
              const updatedLast: CandleItem = {
                ...last,
                close: data.price,
                high: Math.max(last.high, data.price),
                low: Math.min(last.low, data.price),
              };
              return [...prev.slice(0, -1), updatedLast];
            });
          }
        } catch {
          // mensaje no JSON
        }
      };

      ws.onerror = () => {
        setWsConnected(false);
      };

      ws.onclose = () => {
        setWsConnected(false);
        if (pingInterval) clearInterval(pingInterval);
        if (!isUnmounted) {
          const delay = Math.min(1000 * Math.pow(1.5, retryCount), 10000);
          retryCount++;
          reconnectTimer = setTimeout(connectWs, delay);
        }
      };
    };

    connectWs();

    return () => {
      isUnmounted = true;
      if (pingInterval) clearInterval(pingInterval);
      if (reconnectTimer) clearTimeout(reconnectTimer);
      if (ws) ws.close();
    };
  }, [selectedSymbol]);

  // Carga de velas al cambiar símbolo o temporalidad
  useEffect(() => {
    setIsLoadingCandles(true);
    const encoded = encodeURIComponent(selectedSymbol);
    fetch(`/api/v1/market/${encoded}/candles?timeframe=${selectedTimeframe}&limit=50`)
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then((data) => {
        const cList: CandleItem[] = data.data || [];
        setCandles(cList);
        if (cList.length > 0) {
          setLivePrice(cList[cList.length - 1].close);
        }
      })
      .catch((err) => console.error('Error al cargar velas', err))
      .finally(() => setIsLoadingCandles(false));
    refreshResearchContext();
    refreshRegime();
  }, [selectedSymbol, selectedTimeframe]);

  // Acciones del Bot Autónomo
  const handleToggleBot = () => {
    setIsTogglingBot(true);
    const isCurrentlyRunning = autonomousStatus?.is_running;

    if (isCurrentlyRunning) {
      fetch('/api/v1/autonomous/stop', { method: 'POST' })
        .then((res) => res.json())
        .then((data: AutonomousBotStatus) => {
          setAutonomousStatus(data);
          toast.success('⏸️ Asistente autónomo pausado');
        })
        .catch((err) => toast.error(`Error al pausar: ${err.message}`))
        .finally(() => setIsTogglingBot(false));
    } else {
      const riskPct = botRiskLevel === 'conservative' ? 1.0 : botRiskLevel === 'moderate' ? 2.0 : 3.0;
      const environment = runtimeTradingConfig?.trading_environment ?? 'testnet';
      if (botExecutionMode === 'REAL') {
        if (!runtimeTradingConfig?.credentials[environment]) {
          toast.error(`No hay credenciales ${environment.toUpperCase()} configuradas.`);
          setActiveTab('settings');
          setIsTogglingBot(false);
          return;
        }
        if (environment === 'mainnet' && !runtimeTradingConfig?.allow_mainnet_trading) {
          toast.error('Mainnet requiere habilitar el segundo opt-in en Configuración.');
          setActiveTab('settings');
          setIsTogglingBot(false);
          return;
        }
        const venue = environment === 'mainnet' ? 'BINANCE MAINNET (dinero real)' : 'BINANCE TESTNET (fondos de prueba)';
        const notionalLimit = safeLoc(Number(runtimeTradingConfig?.real_trading_max_notional));
        if (!window.confirm(
          `Vas a autorizar al asistente autónomo a enviar órdenes en ${venue} durante esta sesión.\n\n` +
          `Par: ${selectedSymbol} · Intervalo: ${botInterval}s · Puntuación mínima del comité: ${botMinConfidence}%\n` +
          `Riesgo solicitado al stop: ${riskPct}% del saldo (sujeto al perfil de riesgo) · Límite por orden: ${notionalLimit} USDT\n` +
          `Posiciones simultáneas: ${botMaxOpenPositions}. Stop/objetivo: 2%/4% (RR 2:1).\n\n` +
          'El Risk Guard, la allowlist y las protecciones siguen activos. ¿Confirmas iniciar?'
        )) {
          setIsTogglingBot(false);
          return;
        }
      }
      const payload: AutonomousBotConfig = {
        symbol: selectedSymbol,
        timeframe: selectedTimeframe,
        interval_seconds: botInterval,
        execution_mode: botExecutionMode.toLowerCase(),
        confirm_real_trading: botExecutionMode === 'REAL',
        min_confidence_pct: botMinConfidence,
        risk_per_trade_pct: riskPct,
        max_open_positions: botMaxOpenPositions,
        auto_execute: botAutoExecute,
        stop_loss_pct: 2.0,
        take_profit_pct: 4.0,
      };

      (async () => {
        if (botExecutionMode === 'REAL') {
          const configResponse = await fetch('/api/v1/trading/runtime-config', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              execution_mode: 'real',
              enable_real_trading: true,
              enable_autonomous_real_trading: true,
              require_manual_confirmation: true,
              confirm: true,
            }),
          });
          const configData = await configResponse.json();
          if (!configResponse.ok) throw new Error(configData.detail || `HTTP ${configResponse.status}`);
          setRuntimeTradingConfig(configData as RuntimeTradingConfigData);
        }
        const response = await fetch('/api/v1/autonomous/start', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
        return data as AutonomousBotStatus;
      })()
        .then((data: AutonomousBotStatus) => {
          setAutonomousStatus(data);
          toast.success(`🚀 ¡Asistente Autónomo Activo para ${payload.symbol}!`);
        })
        .catch((err) => toast.error(`No se pudo iniciar: ${err.message}`))
        .finally(() => setIsTogglingBot(false));
    }
  };

  const handleEmergencyStop = () => {
    if (
      !window.confirm(
        '⚠️ ¿Estás seguro de activar la PARADA DE EMERGENCIA? Se detendrá el bot y se cerrarán todas las operaciones inmediatamente.'
      )
    ) {
      return;
    }
    fetch('/api/v1/autonomous/emergency-kill', { method: 'POST' })
      .then((res) => res.json())
      .then((data: AutonomousBotStatus) => {
        setAutonomousStatus(data);
        toast.error('🚨 PARADA DE EMERGENCIA EJECUTADA: Bot detenido y posiciones cerradas.');
      })
      .catch((err) => toast.error(`Error de parada: ${err.message}`));
  };

  // Análisis bajo demanda del comité
  const handleRunAIAnalysis = () => {
    setIsAnalyzingAI(true);
    fetch('/api/v1/agents/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        symbol: selectedSymbol,
        timeframe: selectedTimeframe,
        lookback_candles: 50,
      }),
    })
      .then((res) => {
        if (!res.ok) return res.json().then((e) => { throw new Error(e.detail || `HTTP ${res.status}`); });
        return res.json();
      })
      .then((data: CoordinatorDecisionData) => {
        setAiDecision(data);
        const signalTraducida =
          data.final_signal === 'buy' || data.final_signal === 'strong_buy'
            ? 'COMPRAR'
            : data.final_signal === 'sell' || data.final_signal === 'strong_sell'
            ? 'VENDER'
            : 'ESPERAR / NEUTRO';
        toast.success(`Veredicto del Comité: ${signalTraducida} (puntuación ${Math.round(safeNum(data.aggregate_confidence) * 100)}%; no es probabilidad de éxito)`);
      })
      .catch((err) => toast.error(`Error en análisis: ${err.message}`))
      .finally(() => setIsAnalyzingAI(false));
  };

  // Guardar credenciales de exchange
  const handleSaveCredential = () => {
    if (!vaultApiKey || !vaultApiSecret) {
      toast.error('Ingresa la API Key y API Secret');
      return;
    }
    setIsSavingVault(true);
    fetch('/api/v1/vault/keys', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        exchange: 'binance',
        api_key: vaultApiKey,
        api_secret: vaultApiSecret,
        is_testnet: vaultIsTestnet,
      }),
    })
      .then((res) => {
        if (!res.ok) return res.json().then((e) => { throw new Error(e.detail || `HTTP ${res.status}`); });
        return res.json();
      })
      .then(() => {
        toast.success('🔒 Claves cifradas y guardadas con éxito (AES-256)');
        setVaultApiKey('');
        setVaultApiSecret('');
        refreshVault();
      })
      .catch((err) => toast.error(`Error al guardar: ${err.message}`))
      .finally(() => setIsSavingVault(false));
  };

  const handleTestConnection = () => {
    setIsTestingConn(true);
    fetch('/api/v1/vault/test-connection', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ exchange: 'binance', is_testnet: vaultIsTestnet }),
    })
      .then((res) => {
        if (!res.ok) return res.json().then((e) => { throw new Error(e.detail || `HTTP ${res.status}`); });
        return res.json();
      })
      .then((data: ConnectionPingResult) => {
        setPingResult(data);
        toast.success(`🟢 Conexión con Binance OK (Latencia: ${data.latency_ms} ms)`);
      })
      .catch((err) => toast.error(`Fallo de conexión: ${err.message}`))
      .finally(() => setIsTestingConn(false));
  };

  // Traducción amigable de la señal para personas sin experiencia
  const getHumanFriendlySignal = (sig?: string | null) => {
    const s = (sig || '').toUpperCase();
    if (s.includes('BUY')) {
      return {
        label: 'OPORTUNIDAD DE COMPRA',
        color: '#10b981',
        icon: '🟢',
        advice: 'Los analistas detectan fuerza compradora y recomiendan entrar.',
      };
    }
    if (s.includes('SELL')) {
      return {
        label: 'OPORTUNIDAD DE VENTA / SALIDA',
        color: '#ef4444',
        icon: '🔴',
        advice: 'Los analistas detectan presión vendedora o peligro de caída.',
      };
    }
    return {
      label: 'MOMENTO DE ESPERA (NEUTRAL)',
      color: '#94a3b8',
      icon: '⏳',
      advice: 'El mercado está consolidando en rango. El bot vigila el capital y espera un movimiento claro.',
    };
  };

  const currentBotSignal = getHumanFriendlySignal(autonomousStatus?.last_signal);

  return (
    <div className="dashboard-container">
      <Toaster position="top-right" />

      {/* ====================================================================
          BARRA SUPERIOR (HEADER) LIMPIA & ELEGANTE
          ==================================================================== */}
      <header className="dashboard-header" style={{ marginBottom: '16px', paddingBottom: '16px' }}>
        <div className="header-title-group" style={{ display: 'flex', alignItems: 'center', gap: '14px' }}>
          <div
            style={{
              width: '42px',
              height: '42px',
              borderRadius: '12px',
              background: 'linear-gradient(135deg, #3b82f6 0%, #8b5cf6 100%)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontSize: '22px',
              boxShadow: '0 4px 15px rgba(59, 130, 246, 0.4)',
            }}
          >
            🤖
          </div>
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <h1 style={{ margin: 0, fontSize: '22px', fontWeight: 800, letterSpacing: '-0.5px' }}>
                AI Trading Assistant
              </h1>
              <span className="app-badge" style={{ padding: '2px 8px', fontSize: '11px' }}>
                PRO v2.0
              </span>
            </div>
            <p style={{ margin: 0, fontSize: '12px', color: '#94a3b8' }}>
              Trading Algorítmico Autónomo con Asistencia Multi-Agente
            </p>
          </div>
        </div>

        {/* Estado del mercado */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px', flexWrap: 'wrap' }}>
          {/* Badge WebSocket en vivo */}
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              padding: '6px 12px',
              borderRadius: '20px',
              fontSize: '12px',
              fontWeight: 700,
              backgroundColor: wsConnected ? 'rgba(16, 185, 129, 0.15)' : 'rgba(239, 68, 68, 0.15)',
              color: wsConnected ? '#10b981' : '#ef4444',
              border: `1px solid ${wsConnected ? 'rgba(16, 185, 129, 0.3)' : 'rgba(239, 68, 68, 0.3)'}`,
            }}
          >
            <span
              style={{
                width: '8px',
                height: '8px',
                borderRadius: '50%',
                backgroundColor: wsConnected ? '#10b981' : '#ef4444',
                boxShadow: wsConnected ? '0 0 8px #10b981' : 'none',
              }}
            />
            {wsConnected ? 'MERCADO EN VIVO' : 'CONECTANDO...'}
          </div>

        </div>
      </header>

      {/* ====================================================================
          BARRA DE PESTAÑAS (TABS) CLARA & MODERNA
          ==================================================================== */}
      <nav className="nav-tabs-bar">
        <button
          type="button"
          className={`nav-tab-btn ${activeTab === 'bot' ? 'active' : ''}`}
          onClick={() => setActiveTab('bot')}
        >
          🤖 Asistente Autónomo
        </button>

        <button
          type="button"
          className={`nav-tab-btn ${activeTab === 'settings' ? 'active' : ''}`}
          onClick={() => setActiveTab('settings')}
        >
          ⚙️ Configuración & Claves
        </button>
        <details className="advanced-nav">
          <summary>Avanzado</summary>
          <div className="advanced-nav-menu">
            <button type="button" className="nav-tab-btn" onClick={(event) => { setActiveTab('agents'); event.currentTarget.closest('details')?.removeAttribute('open'); }}>Comité de analistas IA</button>
            <button type="button" className="nav-tab-btn" onClick={(event) => { setActiveTab('mlops'); event.currentTarget.closest('details')?.removeAttribute('open'); }}>Métricas de aprendizaje</button>
          </div>
        </details>
      </nav>

      {/* ====================================================================
          PESTAÑA 1: ASISTENTE AUTÓNOMO (CENTRO PRINCIPAL)
          ==================================================================== */}
      {activeTab === 'bot' && (
        <div>
          {/* Mercado y gráfico integrados en la vista principal */}
          <section className="panel trading-chart-panel">
            <div className="trading-chart-toolbar">
              <div className="trading-pair-title">
                <select
                  aria-label="Par de mercado"
                  className="select-input"
                  value={selectedSymbol}
                  onChange={(event) => setSelectedSymbol(event.target.value)}
                >
                  {AVAILABLE_SYMBOLS.map((symbol) => (
                    <option key={symbol.id} value={symbol.id}>{symbol.name}</option>
                  ))}
                </select>
                <strong>{livePrice ? `$${safeLoc(livePrice, { minimumFractionDigits: 2 })}` : '—'}</strong>
                <span className={wsConnected ? 'market-live-indicator connected' : 'market-live-indicator'}>
                  {wsConnected ? 'En vivo' : 'Conectando'}
                </span>
              </div>
              <div className="trading-timeframes" aria-label="Temporalidad del gráfico">
                {TIMEFRAMES.map((timeframe) => (
                  <button
                    key={timeframe.id}
                    type="button"
                    className={`btn ${selectedTimeframe === timeframe.id ? 'btn-primary' : 'btn-secondary'}`}
                    onClick={() => setSelectedTimeframe(timeframe.id)}
                  >
                    {timeframe.id}
                  </button>
                ))}
              </div>
            </div>
            <div className="integrated-market-chart">
              {isLoadingCandles ? (
                <div style={{ textAlign: 'center', padding: '60px', color: '#94a3b8' }}>Cargando mercado…</div>
              ) : (
                <MarketChart candles={candles} livePrice={livePrice} symbol={selectedSymbol} timeframe={selectedTimeframe} />
              )}
            </div>
          </section>

          {/* Régimen de mercado (solo lectura) */}
          <section className="panel" style={{ padding: '18px 24px', marginTop: '16px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '12px', flexWrap: 'wrap' }}>
              <div>
                <h3 style={{ margin: 0, fontSize: '16px', fontWeight: 800 }}>
                  🧭 Régimen de Mercado{' '}
                  <span style={{ fontSize: '11px', fontWeight: 700, color: '#94a3b8' }}>solo lectura</span>
                </h3>
                <p style={{ margin: '4px 0 0', fontSize: '12px', color: '#94a3b8' }}>
                  {selectedSymbol} · {selectedTimeframe} · clasificación descriptiva de velas cerradas; no es una señal de compra/venta.
                </p>
              </div>
              <div style={{ display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap' }}>
                {regimeReport?.status === 'classified' && regimeReport.trend && (
                  <span
                    style={{
                      padding: '3px 10px',
                      borderRadius: '6px',
                      fontSize: '11px',
                      fontWeight: 800,
                      backgroundColor:
                        regimeReport.trend === 'uptrend'
                          ? 'rgba(16, 185, 129, 0.15)'
                          : regimeReport.trend === 'downtrend'
                            ? 'rgba(239, 68, 68, 0.15)'
                            : 'rgba(148, 163, 184, 0.15)',
                      color:
                        regimeReport.trend === 'uptrend'
                          ? '#10b981'
                          : regimeReport.trend === 'downtrend'
                            ? '#ef4444'
                            : '#94a3b8',
                    }}
                  >
                    {regimeReport.trend === 'uptrend'
                      ? 'TENDENCIA ALCISTA'
                      : regimeReport.trend === 'downtrend'
                        ? 'TENDENCIA BAJISTA'
                        : 'RANGO / LATERAL'}
                  </span>
                )}
                {regimeReport?.status === 'classified' && regimeReport.volatility && (
                  <span
                    style={{
                      padding: '3px 10px',
                      borderRadius: '6px',
                      fontSize: '11px',
                      fontWeight: 800,
                      backgroundColor:
                        regimeReport.volatility === 'high'
                          ? 'rgba(245, 158, 11, 0.15)'
                          : regimeReport.volatility === 'low'
                            ? 'rgba(56, 189, 248, 0.15)'
                            : 'rgba(148, 163, 184, 0.12)',
                      color:
                        regimeReport.volatility === 'high'
                          ? '#f59e0b'
                          : regimeReport.volatility === 'low'
                            ? '#38bdf8'
                            : '#94a3b8',
                    }}
                  >
                    {regimeReport.volatility === 'high'
                      ? 'VOLATILIDAD ALTA'
                      : regimeReport.volatility === 'low'
                        ? 'VOLATILIDAD BAJA'
                        : 'VOLATILIDAD NORMAL'}
                  </span>
                )}
                {regimeReport?.metrics.data_stale && (
                  <span
                    style={{
                      padding: '3px 10px',
                      borderRadius: '6px',
                      fontSize: '11px',
                      fontWeight: 800,
                      backgroundColor: 'rgba(245, 158, 11, 0.15)',
                      color: '#f59e0b',
                    }}
                  >
                    DATOS ANTIGUOS
                  </span>
                )}
                <button
                  type="button"
                  className="btn btn-secondary"
                  style={{ fontSize: '11px', padding: '5px 10px' }}
                  onClick={refreshRegime}
                  disabled={isLoadingRegime}
                >
                  {isLoadingRegime ? 'Calculando…' : 'Actualizar'}
                </button>
              </div>
            </div>

            {regimeReport ? (
              regimeReport.status === 'insufficient_data' ? (
                <p style={{ margin: '12px 0 0', fontSize: '12px', color: '#f59e0b' }}>
                  Datos insuficientes: {regimeReport.reasons.join(' ')}
                </p>
              ) : (
                <>
                  <div className="kpi-grid" style={{ marginTop: '14px' }}>
                    <div className="kpi-card" style={{ padding: '12px' }}>
                      <span className="kpi-label">Velas cerradas usadas</span>
                      <span className="kpi-value">{safeNum(regimeReport.metrics.sample_size)}</span>
                    </div>
                    <div className="kpi-card" style={{ padding: '12px' }}>
                      <span className="kpi-label">Separación EMA20 / EMA50</span>
                      <span className="kpi-value">{safeMetric(regimeReport.metrics.ema_separation_pct)}%</span>
                    </div>
                    <div className="kpi-card" style={{ padding: '12px' }}>
                      <span className="kpi-label">Pendiente EMA20 (10 velas)</span>
                      <span className="kpi-value">{safeMetric(regimeReport.metrics.ema_slope_pct)}%</span>
                    </div>
                    <div className="kpi-card" style={{ padding: '12px' }}>
                      <span className="kpi-label">ATR14 sobre precio</span>
                      <span className="kpi-value">{safeMetric(regimeReport.metrics.atr_pct)}%</span>
                    </div>
                    <div className="kpi-card" style={{ padding: '12px' }}>
                      <span className="kpi-label">Percentil ATR · Fuerza</span>
                      <span className="kpi-value">
                        {safeMetric(regimeReport.metrics.atr_percentile, 0)} ·{' '}
                        {safeMetric(regimeReport.metrics.trend_strength, 0)}/100
                      </span>
                    </div>
                  </div>
                  <p style={{ margin: '12px 0 0', fontSize: '12px', opacity: 0.8 }}>
                    {regimeReport.reasons.join(' · ')}
                  </p>
                  <p style={{ margin: '6px 0 0', fontSize: '11px', opacity: 0.55 }}>
                    {regimeReport.disclaimer} No modifica señales, riesgo ni ejecución.
                  </p>
                </>
              )
            ) : (
              <p style={{ margin: '12px 0 0', fontSize: '12px', color: '#94a3b8' }}>Cargando régimen…</p>
            )}
          </section>

          {/* Tarjeta Hero Principal del Bot */}
          <div className="hero-assistant-card">
            <div className="hero-card-grid">
              {/* Lado izquierdo: Estado y Semáforo */}
              <div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '12px' }}>
                  <span
                    style={{
                      padding: '4px 12px',
                      borderRadius: '20px',
                      fontSize: '12px',
                      fontWeight: 800,
                      backgroundColor: autonomousStatus?.is_running
                        ? 'rgba(16, 185, 129, 0.2)'
                        : 'rgba(148, 163, 184, 0.2)',
                      color: autonomousStatus?.is_running ? '#10b981' : '#94a3b8',
                      display: 'flex',
                      alignItems: 'center',
                      gap: '6px',
                    }}
                  >
                    <span
                      style={{
                        width: '8px',
                        height: '8px',
                        borderRadius: '50%',
                        backgroundColor: autonomousStatus?.is_running ? '#10b981' : '#94a3b8',
                      }}
                    />
                    {autonomousStatus?.is_running ? 'BOT EN EJECUCIÓN CONTINUA' : 'BOT EN PAUSA'}
                  </span>
                  <span style={{ fontSize: '13px', color: '#94a3b8' }}>
                    {botExecutionMode === 'PAPER' ? 'Paper' : `Real · ${runtimeTradingConfig?.trading_environment.toUpperCase() ?? 'TESTNET'}`} · Par: <strong>{selectedSymbol}</strong> · cada {botInterval}s
                  </span>
                </div>

                {/* Banner Visual de la Señal */}
                <div className={`signal-banner-large ${autonomousStatus?.last_signal || 'STANDBY'}`}>
                  <div className="signal-icon-large">{currentBotSignal.icon}</div>
                  <div>
                    <div className="signal-text-title">Diagnóstico del Comité Multi-Agente</div>
                    <div className="signal-text-value" style={{ color: currentBotSignal.color }}>
                      {currentBotSignal.label}
                    </div>
                    <div style={{ fontSize: '13px', color: '#cbd5e1', marginTop: '4px' }}>
                      {currentBotSignal.advice}
                    </div>
                  </div>
                </div>

                {Boolean(autonomousStatus?.config.llm_analysis_interval_seconds) && (
                  <div style={{ fontSize: '12px', color: '#94a3b8', marginTop: '8px' }}>
                    Análisis del comité cada{' '}
                    {Math.round((autonomousStatus?.config.llm_analysis_interval_seconds || 0) / 60)} min
                    {' · '}
                    {autonomousStatus?.last_analysis_at
                      ? `último: ${new Date(autonomousStatus.last_analysis_at).toLocaleTimeString()}`
                      : 'aún sin ejecutar en esta sesión'}
                    {' · '}las salidas y el SL/TP se revisan cada {autonomousStatus?.config.interval_seconds}s
                  </div>
                )}

                {Boolean(autonomousStatus?.config.scan_symbols?.length) && (
                  <div style={{ fontSize: '12px', color: '#94a3b8', marginTop: '4px' }}>
                    Escaneo multimercado:{' '}
                    <strong style={{ color: '#cbd5e1' }}>
                      {(autonomousStatus?.config.scan_symbols || []).join(' · ')}
                    </strong>
                    {autonomousStatus?.last_symbol
                      ? ` · último analizado: ${autonomousStatus.last_symbol}`
                      : ''}
                  </div>
                )}

                {/* Métricas clave en tarjetas pequeñas */}
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '12px', marginTop: '16px' }}>
                  <div className="kpi-card" style={{ padding: '12px' }}>
                    <span className="kpi-label">Precio Evaluado</span>
                    <span className="kpi-value" style={{ fontSize: '16px', color: '#38bdf8' }}>
                      ${safeLoc(autonomousStatus?.last_price || livePrice || 0, { minimumFractionDigits: 2 })}
                    </span>
                  </div>
                  <div className="kpi-card" style={{ padding: '12px' }}>
                    <span className="kpi-label">Posiciones del asistente</span>
                    <span className="kpi-value" style={{ fontSize: '16px' }}>
                      {autonomousStatus?.active_positions_count || 0}
                    </span>
                  </div>
                  <div className="kpi-card" style={{ padding: '12px' }}>
                    <span className="kpi-label">Operaciones de esta sesión</span>
                    <span className="kpi-value" style={{ fontSize: '16px', color: '#10b981' }}>
                      {autonomousStatus?.trades_executed || 0}
                    </span>
                  </div>
                </div>
              </div>

              {/* Lado derecho: Controles Directos */}
              <div
                style={{
                  background: 'rgba(15, 23, 42, 0.7)',
                  padding: '20px',
                  borderRadius: '16px',
                  border: '1px solid rgba(255, 255, 255, 0.08)',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '16px',
                }}
              >
                <h3 style={{ margin: 0, fontSize: '15px', fontWeight: 700, color: '#f8fafc' }}>
                  Configura y ejecuta el asistente
                </h3>

                <div style={{ padding: '12px', borderRadius: '10px', background: botExecutionMode === 'PAPER' ? 'rgba(56, 189, 248, 0.1)' : 'rgba(239, 68, 68, 0.12)', border: `1px solid ${botExecutionMode === 'PAPER' ? 'rgba(56, 189, 248, 0.3)' : 'rgba(239, 68, 68, 0.35)'}` }}>
                  <strong style={{ color: botExecutionMode === 'PAPER' ? '#7dd3fc' : '#fca5a5' }}>
                    {botExecutionMode === 'PAPER' ? 'Modo Paper: simulado, sin dinero real' : `Modo Real: ${runtimeTradingConfig?.trading_environment.toUpperCase() ?? 'TESTNET'}`}
                  </strong>
                  <div style={{ marginTop: '5px', fontSize: '12px', color: '#cbd5e1' }}>
                    {botExecutionMode === 'PAPER' ? 'Recomendado para aprender y validar la estrategia.' : 'El asistente podrá abrir y cerrar órdenes automáticamente durante esta sesión.'}
                  </div>
                </div>

                <label className="input-group">
                  <span className="input-label">Modo del asistente</span>
                  <select className="select-input" value={botExecutionMode} disabled={autonomousStatus?.is_running} onChange={(event) => setBotExecutionMode(event.target.value as 'PAPER' | 'REAL')}>
                    <option value="PAPER">Paper · practicar sin dinero real</option>
                    <option value="REAL">Real · usar el entorno configurado</option>
                  </select>
                </label>
                <details className="advanced-options">
                  <summary>Ajustes de estrategia</summary>
                  <div className="advanced-options-content">
                <label className="input-group">
                  <span className="input-label">Revisar el mercado cada</span>
                  <select className="select-input" value={botInterval} disabled={autonomousStatus?.is_running} onChange={(event) => setBotInterval(Number(event.target.value))}>
                    <option value={30}>30 segundos</option><option value={60}>1 minuto (recomendado)</option><option value={120}>2 minutos</option><option value={300}>5 minutos</option>
                  </select>
                </label>
                <label className="input-group">
                  <span className="input-label">Puntuación mínima de señal (no es probabilidad): {botMinConfidence}%</span>
                  <input type="range" min="50" max="90" step="5" value={botMinConfidence} disabled={autonomousStatus?.is_running} onChange={(event) => setBotMinConfidence(Number(event.target.value))} />
                  <span style={{ fontSize: '11px', color: '#94a3b8' }}>Un umbral mayor reduce operaciones; esta puntuación no está calibrada como probabilidad.</span>
                </label>
                <label className="input-group">
                  <span className="input-label">Máximo de posiciones abiertas</span>
                  <select className="select-input" value={botMaxOpenPositions} disabled={autonomousStatus?.is_running} onChange={(event) => setBotMaxOpenPositions(Number(event.target.value))}>
                    <option value={1}>1 posición (recomendado)</option><option value={2}>2 posiciones</option><option value={3}>3 posiciones</option>
                  </select>
                </label>
                  </div>
                </details>

                {/* Auto-ejecución */}
                <label style={{ display: 'flex', alignItems: 'center', gap: '10px', cursor: 'pointer', fontSize: '13px' }}>
                  <input
                    type="checkbox"
                    checked={botAutoExecute}
                    onChange={(e) => setBotAutoExecute(e.target.checked)}
                    disabled={autonomousStatus?.is_running}
                  />
                  <span>El asistente ejecutará automáticamente sus señales</span>
                </label>

                {/* Botón Principal de Encendido/Apagado */}
                <button
                  type="button"
                  className={`btn ${autonomousStatus?.is_running ? 'btn-secondary' : 'btn-primary'}`}
                  style={{
                    padding: '14px',
                    fontSize: '15px',
                    fontWeight: 800,
                    width: '100%',
                    justifyContent: 'center',
                    background: autonomousStatus?.is_running
                      ? 'rgba(239, 68, 68, 0.15)'
                      : 'linear-gradient(135deg, #10b981 0%, #059669 100%)',
                    borderColor: autonomousStatus?.is_running ? '#ef4444' : '#10b981',
                    color: autonomousStatus?.is_running ? '#ef4444' : '#ffffff',
                  }}
                  onClick={handleToggleBot}
                  disabled={isTogglingBot}
                >
                  {isTogglingBot
                    ? 'Procesando...'
                    : autonomousStatus?.is_running
                    ? '⏸ Pausar Asistente'
                    : '▶ Iniciar Asistente Autónomo'}
                </button>

                {/* Ajustes técnicos ocultos por defecto */}
                <details className="advanced-options">
                  <summary>Tamaño de posición</summary>
                  <div className="advanced-options-content">
                  <span style={{ fontSize: '12px', color: '#94a3b8', fontWeight: 600 }}>
                    Tamaño máximo de cada posición (% del saldo)
                  </span>
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '6px', marginTop: '6px' }}>
                    <button
                      type="button"
                      className={`btn ${botRiskLevel === 'conservative' ? 'btn-primary' : 'btn-secondary'}`}
                      style={{ padding: '6px 8px', fontSize: '11px', justifyContent: 'center' }}
                      onClick={() => setBotRiskLevel('conservative')}
                    >
                      🛡️ 1% Conservador
                    </button>
                    <button
                      type="button"
                      className={`btn ${botRiskLevel === 'moderate' ? 'btn-primary' : 'btn-secondary'}`}
                      style={{ padding: '6px 8px', fontSize: '11px', justifyContent: 'center' }}
                      onClick={() => setBotRiskLevel('moderate')}
                    >
                      ⚖️ 2% Moderado
                    </button>
                    <button
                      type="button"
                      className={`btn ${botRiskLevel === 'aggressive' ? 'btn-primary' : 'btn-secondary'}`}
                      style={{ padding: '6px 8px', fontSize: '11px', justifyContent: 'center' }}
                      onClick={() => setBotRiskLevel('aggressive')}
                    >
                      ⚡ 3% Alto
                    </button>
                  </div>
                  </div>
                </details>

                <span style={{ marginTop: '-10px', fontSize: '11px', color: '#94a3b8' }}>En Real se solicita una confirmación antes de iniciar. El bot usa Stop 2% y objetivo 4%.</span>

                {/* Botón de Emergencia */}
                <details className="advanced-options">
                  <summary>Controles de emergencia</summary>
                <button
                  type="button"
                  className="btn btn-danger"
                  style={{
                    padding: '8px',
                    fontSize: '12px',
                    justifyContent: 'center',
                    background: 'rgba(239, 68, 68, 0.12)',
                    borderColor: 'rgba(239, 68, 68, 0.3)',
                    color: '#f87171',
                  }}
                  onClick={handleEmergencyStop}
                >
                  🚨 Detener y cerrar posiciones del bot
                </button>
                </details>
              </div>
            </div>
          </div>

          {/* Registro de Actividad y Pensamiento del Bot */}
          <details className="advanced-options advanced-log">
            <summary>Registro técnico de decisiones ({autonomousStatus?.logs?.length || 0})</summary>
            <div className="panel" style={{ padding: '20px', marginTop: '12px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '14px' }}>
              <div>
                <h3 style={{ margin: 0, fontSize: '16px', fontWeight: 800 }}>
                  📜 Registro de Decisiones y Actividad del Bot
                </h3>
                <p style={{ margin: '4px 0 0', fontSize: '12px', color: '#94a3b8' }}>
                  Historial explicativo de lo que los agentes analizan en cada ciclo.
                </p>
              </div>
              <span style={{ fontSize: '12px', color: '#38bdf8', fontWeight: 600 }}>
                {autonomousStatus?.logs?.length || 0} eventos registrados
              </span>
            </div>

            <div className="bot-logs-stream" style={{ maxHeight: '340px' }}>
              {!autonomousStatus?.logs || autonomousStatus.logs.length === 0 ? (
                <div style={{ textAlign: 'center', padding: '30px', color: '#94a3b8', fontSize: '14px' }}>
                  El bot no ha registrado eventos aún. Presiona <strong>"Iniciar Asistente Autónomo"</strong> arriba para comenzar.
                </div>
              ) : (
                autonomousStatus.logs.slice(0, 25).map((log) => (
                  <div key={log.id} className={`bot-log-row ${log.level}`}>
                    <span className="bot-log-time">
                      {new Date(log.timestamp).toLocaleTimeString()}
                    </span>
                    <span className={`bot-log-badge ${log.level}`}>
                      {log.level === 'INFO' ? 'ANÁLISIS' : log.level === 'WARNING' ? 'ALERTA' : 'ERROR'}
                    </span>
                    <div style={{ flex: 1, fontSize: '13px' }}>
                      <span>{log.message}</span>
                      {log.price && (
                        <span style={{ color: '#38bdf8', marginLeft: '8px', fontWeight: 600 }}>
                          [Precio: ${safeLoc(log.price)}]
                        </span>
                      )}
                    </div>
                  </div>
                ))
              )}
            </div>
            </div>
          </details>
        </div>
      )}

      {/* ====================================================================
          PESTAÑA 2: MERCADO & GRÁFICO
          ==================================================================== */}
      {activeTab === 'agents' && (
        <div className="panel" style={{ padding: '24px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '18px', flexWrap: 'wrap', gap: '12px' }}>
            <div>
              <h3 style={{ margin: 0, fontSize: '18px', fontWeight: 800 }}>
                🧠 Comité de Inteligencia Artificial
              </h3>
              <p style={{ margin: '4px 0 0', fontSize: '13px', color: '#94a3b8' }}>
                Tres agentes especialistas analizan el mercado de forma independiente y un coordinador toma la decisión final.
              </p>
            </div>

            <button
              type="button"
              className="btn btn-primary"
              onClick={handleRunAIAnalysis}
              disabled={isAnalyzingAI}
            >
              {isAnalyzingAI ? 'El Comité está debatiendo...' : `🔍 Consultar al Comité sobre ${selectedSymbol}`}
            </button>
          </div>

          {/* Veredicto si existe */}
          {aiDecision && (
            <div
              style={{
                background: 'rgba(15, 23, 42, 0.9)',
                padding: '20px',
                borderRadius: '16px',
                border: '2px solid #38bdf8',
                marginBottom: '24px',
              }}
            >
              <span style={{ fontSize: '12px', color: '#38bdf8', fontWeight: 700, textTransform: 'uppercase' }}>
                Consenso Final del Coordinador Ejecutivo
              </span>
              <div style={{ fontSize: '24px', fontWeight: 900, marginTop: '4px', color: '#f8fafc' }}>
                {aiDecision.action_recommendation}
              </div>
              <p style={{ fontSize: '14px', color: '#cbd5e1', marginTop: '8px' }}>
                {aiDecision.reasoning_summary}
              </p>

              {(aiDecision.recommended_stop || aiDecision.recommended_target) && (
                <div style={{ display: 'flex', gap: '16px', marginTop: '12px', flexWrap: 'wrap' }}>
                  {aiDecision.recommended_stop && (
                    <span style={{ fontSize: '13px', color: '#ef4444' }}>
                      🛡️ Stop Loss Recomendado: <strong>${safeLoc(aiDecision.recommended_stop)}</strong>
                    </span>
                  )}
                  {aiDecision.recommended_target && (
                    <span style={{ fontSize: '13px', color: '#10b981' }}>
                      🎯 Take Profit Objetivo: <strong>${safeLoc(aiDecision.recommended_target)}</strong>
                    </span>
                  )}
                </div>
              )}
            </div>
          )}

          {/* Tarjetas de los 3 Especialistas */}
          <div className="specialist-cards-grid">
            {/* Especialista Técnico */}
            <div className="specialist-card">
              <div className="specialist-card-header">
                <span className="specialist-name">📈 Analista Técnico Cuantitativo</span>
                <span className="specialist-badge" style={{ backgroundColor: 'rgba(59, 130, 246, 0.2)', color: '#60a5fa' }}>
                  PESO: 40%
                </span>
              </div>
              <p className="specialist-desc">
                Examina indicadores matemáticos probados: <strong>RSI</strong> (sobrecompra/sobreventa), <strong>MACD</strong> (fuerza de impulso) y <strong>Medias Móviles EMA (9, 21, 50, 200)</strong> para determinar la tendencia matemática.
              </p>
            </div>

            {/* Especialista de Acción del Precio */}
            <div className="specialist-card">
              <div className="specialist-card-header">
                <span className="specialist-name">🕯️ Experto en Velas & Estructura (SMC)</span>
                <span className="specialist-badge" style={{ backgroundColor: 'rgba(168, 85, 247, 0.2)', color: '#c084fc' }}>
                  PESO: 35%
                </span>
              </div>
              <p className="specialist-desc">
                Analiza zonas de soporte y resistencia, patrones de velas (Pin bar, envolventes) y trampas de liquidez institucionales para evitar entrar en falsas rupturas.
              </p>
            </div>

            {/* Gestor de Riesgo */}
            <div className="specialist-card">
              <div className="specialist-card-header">
                <span className="specialist-name">🛡️ Guardián y Controlador de Riesgo</span>
                <span className="specialist-badge" style={{ backgroundColor: 'rgba(16, 185, 129, 0.2)', color: '#34d399' }}>
                  PESO: 25%
                </span>
              </div>
              <p className="specialist-desc">
                Mide la volatilidad en tiempo real (ATR) y calcula exactamente dónde situar el <strong>Stop Loss</strong> y <strong>Take Profit</strong> manteniendo un ratio mínimo de 2.0 a 1 para proteger el capital.
              </p>
            </div>
          </div>
        </div>
      )}

      {/* ====================================================================
          PESTAÑA 5: MLOPS / APRENDIZAJE CONTINUO
          ==================================================================== */}
      {activeTab === 'mlops' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
          <div className="panel" style={{ padding: '24px' }}>
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                marginBottom: '18px',
                flexWrap: 'wrap',
                gap: '12px',
              }}
            >
              <div>
                <h3 style={{ margin: 0, fontSize: '18px', fontWeight: 800 }}>
                  📊 MLOps & Aprendizaje Continuo
                </h3>
                <p style={{ margin: '4px 0 0', fontSize: '13px', color: '#94a3b8' }}>
                  Atribución real de resultados a las decisiones del comité. Sin datos simulados:
                  solo órdenes y decisiones persistidas en el journal.
                </p>
              </div>

              <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap', alignItems: 'center' }}>
                <select
                  className="select-input"
                  style={{ padding: '8px 12px', fontSize: '13px' }}
                  value={mlopsMode}
                  onChange={(e) => setMlopsMode(e.target.value as 'all' | 'paper' | 'real')}
                >
                  <option value="all">Todos los modos</option>
                  <option value="paper">Paper</option>
                  <option value="real">Real</option>
                </select>

                <select
                  className="select-input"
                  style={{ padding: '8px 12px', fontSize: '13px' }}
                  value={mlopsSymbol}
                  onChange={(e) => setMlopsSymbol(e.target.value)}
                >
                  <option value="">Todos los símbolos</option>
                  {AVAILABLE_SYMBOLS.map((symbol) => (
                    <option key={symbol.id} value={symbol.id}>
                      {symbol.icon} {symbol.name}
                    </option>
                  ))}
                </select>

                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={refreshMlopsPerformance}
                  disabled={isLoadingMlops}
                >
                  {isLoadingMlops ? 'Actualizando...' : '🔄 Actualizar'}
                </button>
              </div>
            </div>

            {!mlopsPerformance ? (
              <p style={{ color: '#94a3b8', fontSize: '14px' }}>
                {isLoadingMlops ? 'Calculando métricas...' : 'Sin métricas disponibles todavía.'}
              </p>
            ) : !mlopsPerformance.data_available ? (
              <div
                style={{
                  padding: '14px 18px',
                  borderRadius: '10px',
                  background: 'rgba(245, 158, 11, 0.12)',
                  border: '1px solid rgba(245, 158, 11, 0.45)',
                  color: '#fbbf24',
                  fontSize: '13px',
                }}
              >
                La base de datos no está disponible en este momento. Las métricas se
                reconstruirán cuando vuelva la conexión.
              </div>
            ) : (
              <>
                <div className="kpi-grid">
                  <div className="kpi-card">
                    <div className="kpi-label">Decisiones analizadas</div>
                    <div className="kpi-value">{safeLoc(mlopsPerformance.total_decisions)}</div>
                  </div>
                  <div className="kpi-card">
                    <div className="kpi-label">Ejecutadas</div>
                    <div className="kpi-value">{safeLoc(mlopsPerformance.executed)}</div>
                  </div>
                  <div className="kpi-card">
                    <div className="kpi-label">Resueltas</div>
                    <div className="kpi-value">{safeLoc(mlopsPerformance.resolved)}</div>
                  </div>
                  <div className="kpi-card">
                    <div className="kpi-label">Tasa de acierto</div>
                    <div className="kpi-value positive">
                      {safeFix(mlopsPerformance.win_rate_pct, 1)}%
                    </div>
                  </div>
                  <div className="kpi-card">
                    <div className="kpi-label">PnL neto realizado</div>
                    <div
                      className={`kpi-value ${
                        safeNum(mlopsPerformance.net_pnl) >= 0 ? 'positive' : 'negative'
                      }`}
                    >
                      ${safeLoc(mlopsPerformance.net_pnl)}
                    </div>
                  </div>
                  <div className="kpi-card">
                    <div className="kpi-label">Factor de beneficio</div>
                    <div className="kpi-value">
                      {mlopsPerformance.profit_factor === null
                        ? '—'
                        : safeFix(mlopsPerformance.profit_factor, 2)}
                    </div>
                  </div>
                  <div className="kpi-card">
                    <div className="kpi-label">Puntuación media del comité</div>
                    <div className="kpi-value">
                      {safeFix(safeNum(mlopsPerformance.avg_confidence) * 100, 1)}%
                    </div>
                  </div>
                  <div className="kpi-card">
                    <div className="kpi-label">Órdenes sin atribuir</div>
                    <div className="kpi-value">{safeLoc(mlopsPerformance.unattributed_orders)}</div>
                  </div>
                </div>

                <p style={{ margin: '14px 0 0', fontSize: '12px', color: '#64748b' }}>
                  Solo se puntúan decisiones con entrada y salida completas. El Risk Manager se
                  excluye de la precisión direccional. Las comisiones en BNB no se convierten
                  automáticamente. Las tasas por rango son descriptivas, no una calibración ni
                  una probabilidad de éxito.
                </p>
              </>
            )}
          </div>

          {mlopsPerformance?.data_available && (
            <>
              <div className="panel" style={{ padding: '24px' }}>
                <h3 style={{ margin: '0 0 14px', fontSize: '16px', fontWeight: 800 }}>
                  🧠 Precisión por agente
                </h3>
                {mlopsPerformance.by_agent.length === 0 ? (
                  <p style={{ color: '#94a3b8', fontSize: '13px' }}>
                    Aún no hay decisiones cerradas para evaluar a los agentes.
                  </p>
                ) : (
                  <div className="table-container">
                    <table className="candles-table">
                      <thead>
                        <tr>
                          <th>Agente</th>
                          <th>Acierto</th>
                          <th>Aciertos / evaluadas</th>
                          <th>Abstenciones</th>
                          <th>Puntuación media del agente</th>
                          <th>Latencia media</th>
                        </tr>
                      </thead>
                      <tbody>
                        {mlopsPerformance.by_agent.map((agent) => (
                          <tr key={agent.agent_type}>
                            <td>{AGENT_LABELS[agent.agent_type] ?? agent.agent_type}</td>
                            <td>{safeFix(agent.hit_rate_pct, 1)}%</td>
                            <td>
                              {agent.correct} / {agent.decisions_scored}
                            </td>
                            <td>{agent.abstained}</td>
                            <td>{safeFix(safeNum(agent.avg_confidence) * 100, 1)}%</td>
                            <td>{safeFix(agent.avg_processing_time_ms, 0)} ms</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>

              <div
                style={{
                  display: 'grid',
                  gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))',
                  gap: '20px',
                }}
              >
                <div className="panel" style={{ padding: '24px' }}>
                  <h3 style={{ margin: '0 0 14px', fontSize: '16px', fontWeight: 800 }}>
                    🎯 Rendimiento por señal
                  </h3>
                  {mlopsPerformance.by_signal.length === 0 ? (
                    <p style={{ color: '#94a3b8', fontSize: '13px' }}>Sin datos todavía.</p>
                  ) : (
                    <div className="table-container">
                      <table className="candles-table">
                        <thead>
                          <tr>
                            <th>Señal</th>
                            <th>Decisiones</th>
                            <th>Acierto</th>
                            <th>PnL neto</th>
                          </tr>
                        </thead>
                        <tbody>
                          {mlopsPerformance.by_signal.map((item) => (
                            <tr key={item.signal}>
                              <td>{SIGNAL_LABELS[item.signal] ?? item.signal}</td>
                              <td>{item.decisions}</td>
                              <td>
                                {safeFix(item.hit_rate_pct, 1)}% ({item.correct}/{item.resolved})
                              </td>
                              <td
                                style={{
                                  color: safeNum(item.net_pnl) >= 0 ? '#10b981' : '#ef4444',
                                }}
                              >
                                ${safeLoc(item.net_pnl)}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>

                <div className="panel" style={{ padding: '24px' }}>
                  <h3 style={{ margin: '0 0 14px', fontSize: '16px', fontWeight: 800 }}>
                    💹 Rendimiento por símbolo
                  </h3>
                  {mlopsPerformance.by_symbol.length === 0 ? (
                    <p style={{ color: '#94a3b8', fontSize: '13px' }}>Sin datos todavía.</p>
                  ) : (
                    <div className="table-container">
                      <table className="candles-table">
                        <thead>
                          <tr>
                            <th>Símbolo</th>
                            <th>Decisiones</th>
                            <th>Win rate</th>
                            <th>PnL neto</th>
                          </tr>
                        </thead>
                        <tbody>
                          {mlopsPerformance.by_symbol.map((item) => (
                            <tr key={item.symbol}>
                              <td>{item.symbol}</td>
                              <td>{item.decisions}</td>
                              <td>
                                {safeFix(item.win_rate_pct, 1)}% ({item.resolved} resueltas)
                              </td>
                              <td
                                style={{
                                  color: safeNum(item.net_pnl) >= 0 ? '#10b981' : '#ef4444',
                                }}
                              >
                                ${safeLoc(item.net_pnl)}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              </div>

              <div className="panel" style={{ padding: '24px' }}>
                <h3 style={{ margin: '0 0 14px', fontSize: '16px', fontWeight: 800 }}>
                  📈 Resultados por rango de puntuación
                </h3>
                <div className="table-container">
                  <table className="candles-table">
                    <thead>
                      <tr>
                        <th>Rango de puntuación</th>
                        <th>Decisiones cerradas</th>
                        <th>Aciertos</th>
                        <th>Tasa de acierto</th>
                        <th>Evidencia</th>
                      </tr>
                    </thead>
                    <tbody>
                      {mlopsPerformance.confidence_buckets.map((bucket) => (
                        <tr key={`${bucket.lower}-${bucket.upper}`}>
                          <td>
                            {safeFix(bucket.lower * 100, 0)}% – {safeFix(bucket.upper * 100, 0)}%
                          </td>
                          <td>{bucket.decisions}</td>
                          <td>{bucket.correct}</td>
                          <td>{safeFix(bucket.hit_rate_pct, 1)}%</td>
                          <td>
                            {bucket.sample_sufficient
                              ? `Exploratoria (n=${bucket.decisions})`
                              : `Muestra insuficiente (${bucket.decisions}/${bucket.minimum_sample_size})`}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </>
          )}
        </div>
      )}

      {/* ====================================================================
          PESTAÑA 6: CONFIGURACIÓN & CLAVES (SIN RUIDO)
          ==================================================================== */}
      {activeTab === 'settings' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
          {/* Conexión de Exchange */}
          <div className="panel" style={{ padding: '24px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
              <div>
                <h3 style={{ margin: 0, fontSize: '18px', fontWeight: 800 }}>
                  🔐 Conexión con Binance (Claves de API)
                </h3>
                <p style={{ margin: '4px 0 0', fontSize: '13px', color: '#94a3b8' }}>
                  Solo necesario si deseas operar con fondos de prueba en Testnet o con saldo Real en Binance.
                </p>
              </div>
              <span style={{ fontSize: '12px', color: '#10b981', fontWeight: 700 }}>
                🔒 Cifrado Simétrico AES-256
              </span>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '14px', maxWidth: '750px', marginBottom: '14px' }}>
              <div className="input-group">
                <span className="input-label">Binance API Key:</span>
                <input
                  type="text"
                  className="text-input"
                  placeholder="Pega tu API Key..."
                  value={vaultApiKey}
                  onChange={(e) => setVaultApiKey(e.target.value)}
                />
              </div>

              <div className="input-group">
                <span className="input-label">Binance Secret Key:</span>
                <input
                  type="password"
                  className="text-input"
                  placeholder="Pega tu API Secret..."
                  value={vaultApiSecret}
                  onChange={(e) => setVaultApiSecret(e.target.value)}
                />
              </div>
            </div>

            <div style={{ display: 'flex', alignItems: 'center', gap: '16px', flexWrap: 'wrap' }}>
              <label style={{ display: 'flex', alignItems: 'center', gap: '8px', cursor: 'pointer', fontSize: '13px' }}>
                <input
                  type="checkbox"
                  checked={vaultIsTestnet}
                  onChange={(e) => setVaultIsTestnet(e.target.checked)}
                />
                <span>Usar Binance Testnet (Red de pruebas sin dinero real)</span>
              </label>

              <button
                type="button"
                className="btn btn-primary"
                onClick={handleSaveCredential}
                disabled={isSavingVault}
              >
                {isSavingVault ? 'Cifrando...' : '💾 Guardar Claves Seguras'}
              </button>

              <button
                type="button"
                className="btn btn-secondary"
                onClick={handleTestConnection}
                disabled={isTestingConn}
              >
                {isTestingConn ? 'Comprobando...' : '⚡ Probar Conexión (Ping)'}
              </button>
            </div>

            {/* Resultado del Ping */}
            {pingResult && (
              <div style={{ marginTop: '16px', padding: '12px 16px', background: 'rgba(16, 185, 129, 0.1)', borderRadius: '10px', border: '1px solid rgba(16, 185, 129, 0.3)' }}>
                <span style={{ color: '#10b981', fontWeight: 700 }}>🟢 Conexión Verificada:</span>{' '}
                <span style={{ color: '#f8fafc' }}>Binance ({pingResult.is_testnet ? 'Testnet' : 'Principal'}) • Latencia: {pingResult.latency_ms} ms</span>
              </div>
            )}

            {/* Credenciales configuradas */}
            {vaultCredentials.length > 0 && (
              <div style={{ marginTop: '16px', borderTop: '1px solid rgba(255, 255, 255, 0.08)', paddingTop: '12px' }}>
                <span style={{ fontSize: '12px', color: '#94a3b8', fontWeight: 600 }}>Claves Registradas en el Sistema:</span>
                <div style={{ display: 'flex', gap: '8px', marginTop: '6px', flexWrap: 'wrap' }}>
                  {vaultCredentials.map((c) => (
                    <span
                      key={c.masked_key}
                      style={{
                        padding: '4px 10px',
                        borderRadius: '6px',
                        fontSize: '11px',
                        background: 'rgba(255, 255, 255, 0.05)',
                        border: '1px solid rgba(255, 255, 255, 0.1)',
                      }}
                    >
                      {c.exchange.toUpperCase()} ({c.masked_key}) • {c.is_testnet ? 'Testnet' : 'Mainnet'}
                    </span>
                  ))}
                </div>
              </div>
            )}
          </div>

          {/* Control de ejecución administrado desde la app */}
          {runtimeTradingConfig && (
            <div className="panel" style={{ padding: '24px' }}>
              <details className="advanced-options">
              <summary>Entorno, límites y protecciones</summary>
              <div className="advanced-options-content">
              <p style={{ margin: '0 0 16px', fontSize: '13px', color: '#94a3b8' }}>
                Opciones para usuarios avanzados. Los límites de riesgo y las claves se conservan separados por entorno.
              </p>
              <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap', alignItems: 'center' }}>
                <label className="input-group" style={{ minWidth: '190px' }}>
                  <span className="input-label">Red del exchange</span>
                  <select
                    className="select-input"
                    value={runtimeTradingConfig.trading_environment}
                    disabled={isSavingRuntimeConfig}
                    onChange={async (event) => {
                      const environment = event.target.value as 'testnet' | 'mainnet';
                      if (environment === 'mainnet') {
                        if (!runtimeTradingConfig?.credentials.mainnet) {
                          toast.error('Primero guarda y prueba tus claves Mainnet en el panel de credenciales.');
                          return;
                        }
                      }
                      await saveRuntimeConfig({ trading_environment: environment }, environment === 'mainnet');
                    }}
                  >
                    <option value="testnet">Testnet (recomendado)</option>
                    <option value="mainnet">Mainnet (dinero real)</option>
                  </select>
                </label>
                <label className="input-group" style={{ minWidth: '150px' }}>
                  <span className="input-label">Máximo por orden (USDT)</span>
                  <input className="text-input" type="number" min="1" step="1" value={runtimeMaxNotional} onChange={(event) => setRuntimeMaxNotional(event.target.value)} />
                </label>
                <label className="input-group" style={{ minWidth: '220px', flex: 1 }}>
                  <span className="input-label">Allowlist de símbolos</span>
                  <input className="text-input" value={runtimeAllowedSymbols} onChange={(event) => setRuntimeAllowedSymbols(event.target.value)} placeholder="BTC/USDT, ETH/USDT" />
                </label>
              </div>
              <div style={{ display: 'flex', gap: '18px', flexWrap: 'wrap', margin: '15px 0', fontSize: '13px' }}>
                <label style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                  <input type="checkbox" checked={runtimeTradingConfig.allow_mainnet_trading} onChange={(event) => {
                    void saveRuntimeConfig({ allow_mainnet_trading: event.target.checked }, event.target.checked);
                  }} />
                  <span>Segundo opt-in: permitir Mainnet</span>
                </label>
                <label style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                  <input type="checkbox" checked={runtimeAutonomousReal} onChange={(event) => setRuntimeAutonomousReal(event.target.checked)} />
                  <span>Permitir ejecución real autónoma</span>
                </label>
                <label style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                  <input type="checkbox" checked={runtimeOcoEnabled} onChange={(event) => setRuntimeOcoEnabled(event.target.checked)} />
                  <span>Protección OCO nativa (Binance Spot)</span>
                </label>
                <span style={{ color: '#10b981', fontWeight: 700 }}>Confirmación manual por orden: siempre activa</span>
              </div>
              <button
                type="button"
                className="btn btn-primary"
                disabled={isSavingRuntimeConfig}
                onClick={() => void saveRuntimeConfig({
                  real_trading_max_notional: Number(runtimeMaxNotional),
                  real_trading_allowed_symbols: runtimeAllowedSymbols.split(',').map((symbol) => symbol.trim()).filter(Boolean),
                  enable_autonomous_real_trading: runtimeAutonomousReal,
                  real_trading_oco_enabled: runtimeOcoEnabled,
                  require_manual_confirmation: true,
                })}
              >
                {isSavingRuntimeConfig ? 'Guardando…' : 'Guardar límites y protecciones'}
              </button>
              <span style={{ marginLeft: '12px', color: '#94a3b8', fontSize: '12px' }}>
                Claves Testnet: {runtimeTradingConfig.credentials.testnet ? 'configuradas' : 'faltan'} · Mainnet: {runtimeTradingConfig.credentials.mainnet ? 'configuradas' : 'faltan'}
              </span>
              </div>
              </details>
            </div>
          )}

          {/* Trading Real y Alertas */}
          <details className="advanced-options">
            <summary>Diagnóstico, protecciones y alertas</summary>
            <div className="advanced-options-content" style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
          <div className="panel" style={{ padding: '24px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '14px', gap: '12px', flexWrap: 'wrap' }}>
              <div>
                <h3 style={{ margin: 0, fontSize: '16px', fontWeight: 800 }}>
                  🛡️ Trading Real y Alertas
                </h3>
                <p style={{ margin: '4px 0 0', fontSize: '13px', color: '#94a3b8' }}>
                  Salvaguardas de ejecución y notificaciones. El trading real requiere confirmación por orden y respeta el límite configurado.
                </p>
              </div>
              <span
                style={{
                  padding: '4px 10px',
                  borderRadius: '6px',
                  fontSize: '11px',
                  fontWeight: 800,
                  backgroundColor: realTradingStatus?.enabled ? 'rgba(16, 185, 129, 0.2)' : 'rgba(148, 163, 184, 0.15)',
                  color: realTradingStatus?.enabled ? '#10b981' : '#94a3b8',
                  border: `1px solid ${realTradingStatus?.enabled ? 'rgba(16, 185, 129, 0.4)' : 'rgba(148, 163, 184, 0.3)'}`,
                }}
              >
                {realTradingStatus?.enabled ? 'REAL HABILITADO' : 'SOLO PAPER'}
              </span>
            </div>

            {realTradingStatus ? (
              <>
                <div className="kpi-grid">
                  <div className="kpi-card" style={{ padding: '12px' }}>
                    <span className="kpi-label">Exchange</span>
                    <span className="kpi-value" style={{ textTransform: 'uppercase' }}>{realTradingStatus.exchange}</span>
                  </div>
                  <div className="kpi-card" style={{ padding: '12px' }}>
                    <span className="kpi-label">Entorno</span>
                    <span className="kpi-value" style={{ color: realTradingStatus.testnet ? '#38bdf8' : '#ef4444' }}>
                      {realTradingStatus.testnet ? 'TESTNET' : 'MAINNET'}
                    </span>
                  </div>
                  <div className="kpi-card" style={{ padding: '12px' }}>
                    <span className="kpi-label">Credenciales</span>
                    <span className="kpi-value" style={{ color: realTradingStatus.credentials_configured ? '#10b981' : '#ef4444' }}>
                      {realTradingStatus.credentials_configured ? 'CONFIGURADAS' : 'FALTAN'}
                    </span>
                  </div>
                  <div className="kpi-card" style={{ padding: '12px' }}>
                    <span className="kpi-label">Máx. por orden</span>
                    <span className="kpi-value">${safeLoc(Number(realTradingStatus.max_notional), { minimumFractionDigits: 2 })}</span>
                  </div>
                  <div className="kpi-card" style={{ padding: '12px' }}>
                    <span className="kpi-label">Posiciones reales</span>
                    <span className="kpi-value">{realTradingStatus.open_positions}</span>
                  </div>
                  <div className="kpi-card" style={{ padding: '12px' }}>
                    <span className="kpi-label">Guardián SL/TP</span>
                    <span className="kpi-value" style={{ color: realTradingStatus.guard_running ? '#10b981' : '#f59e0b' }}>
                      {realTradingStatus.guard_running ? 'ACTIVO' : 'INACTIVO'}
                    </span>
                  </div>
                  <div className="kpi-card" style={{ padding: '12px' }}>
                    <span className="kpi-label">OCO activos</span>
                    <span className="kpi-value" style={{ color: realTradingStatus.oco_discovery_failed_exchanges?.length ? '#ef4444' : realTradingStatus.oco_enabled ? '#10b981' : '#94a3b8' }}>
                      {realTradingStatus.oco_enabled ? (realTradingStatus.oco_protected_positions ?? 0) : 'OFF'}
                    </span>
                  </div>
                </div>
                <div style={{ marginTop: '12px', fontSize: '12px', color: '#94a3b8' }}>
                  Símbolos permitidos: <strong style={{ color: '#e2e8f0' }}>{realTradingStatus.allowed_symbols.join(', ') || 'sin restricción'}</strong>
                  {' · '}Confirmación manual: <strong style={{ color: '#e2e8f0' }}>{realTradingStatus.require_manual_confirmation ? 'Sí' : 'No'}</strong>
                  {' · '}Bot autónomo real: <strong style={{ color: realTradingStatus.autonomous_real_enabled ? '#f59e0b' : '#94a3b8' }}>{realTradingStatus.autonomous_real_enabled ? 'HABILITADO' : 'deshabilitado'}</strong>
                  {' · '}Protección: <strong style={{ color: realTradingStatus.protection_mode === 'oco_discovery_failed' ? '#ef4444' : '#f59e0b' }}>{realTradingStatus.protection_mode === 'exchange_oco' ? 'OCO exchange-side' : realTradingStatus.protection_mode === 'oco_with_local_fallback' ? 'OCO + guardián local' : realTradingStatus.protection_mode === 'oco_discovery_failed' ? 'OCO sin confirmar (ventas bloqueadas)' : 'Guardián local'}</strong>
                  {realTradingStatus.oco_discovery_failed_exchanges?.length ? (
                    <span style={{ color: '#ef4444' }}>
                      {' · '}Revisar OCO en: {realTradingStatus.oco_discovery_failed_exchanges.join(', ')}
                    </span>
                  ) : null}
                </div>
                <button
                  type="button"
                  className="btn btn-secondary"
                  style={{ marginTop: '10px', fontSize: '12px', padding: '6px 12px' }}
                  onClick={handleReconcileReal}
                  disabled={isReconcilingReal}
                >
                  {isReconcilingReal ? 'Reconciliando...' : 'Reconciliar con exchange'}
                </button>
                {realTradingStatus.enabled && !realTradingStatus.testnet && (
                  <div style={{ marginTop: '12px', padding: '10px 14px', borderRadius: '8px', background: 'rgba(239, 68, 68, 0.12)', border: '1px solid rgba(239, 68, 68, 0.35)', color: '#fca5a5', fontSize: '12px', fontWeight: 700 }}>
                    ⚠️ MAINNET activo: las órdenes confirmadas usarán dinero real.
                  </div>
                )}
              </>
            ) : (
              <p style={{ fontSize: '13px', color: '#94a3b8' }}>No se pudo leer el estado de trading real.</p>
            )}

            <div style={{ marginTop: '18px', paddingTop: '16px', borderTop: '1px solid rgba(255, 255, 255, 0.08)' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '12px', flexWrap: 'wrap' }}>
                <div>
                  <span style={{ fontSize: '13px', fontWeight: 800, color: '#e2e8f0' }}>🔔 Alertas</span>
                  <p style={{ margin: '4px 0 0', fontSize: '12px', color: '#94a3b8' }}>
                    Telegram, webhook y email. Se envían al ejecutar órdenes, activar SL/TP o disparar el kill-switch.
                  </p>
                </div>
                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={handleTestAlert}
                  disabled={isTestingAlert}
                >
                  {isTestingAlert ? 'Enviando...' : 'Enviar alerta de prueba'}
                </button>
              </div>
              {alertStatus && (
                <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', marginTop: '10px', alignItems: 'center' }}>
                  <span style={{ padding: '4px 10px', borderRadius: '6px', fontSize: '11px', background: alertStatus.enabled ? 'rgba(16, 185, 129, 0.15)' : 'rgba(148, 163, 184, 0.12)', color: alertStatus.enabled ? '#10b981' : '#94a3b8', fontWeight: 700 }}>
                    {alertStatus.enabled ? 'ALERTAS ACTIVAS' : 'ALERTAS DESACTIVADAS'}
                  </span>
                  {alertStatus.channels.map((channel) => (
                    <span key={channel.name} style={{ padding: '4px 10px', borderRadius: '6px', fontSize: '11px', background: 'rgba(255, 255, 255, 0.05)', border: '1px solid rgba(255, 255, 255, 0.1)', color: channel.configured ? '#e2e8f0' : '#64748b' }}>
                      {channel.name.toUpperCase()} {channel.configured ? '✓' : '—'}
                    </span>
                  ))}
                  <span style={{ fontSize: '11px', color: '#94a3b8' }}>
                    Enviadas: {alertStatus.sent_count} · Fallidas: {alertStatus.failed_count}
                  </span>
                </div>
              )}
            </div>
          </div>

          {/* Protección y Límites de Riesgo */}
          {riskStatus && (
            <div className="panel" style={{ padding: '24px' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '14px' }}>
                <h3 style={{ margin: 0, fontSize: '16px', fontWeight: 800 }}>
                  🛡️ Cortafuegos y Protección Automática de Riesgo
                </h3>
                <span
                  style={{
                    padding: '3px 10px',
                    borderRadius: '6px',
                    fontSize: '11px',
                    fontWeight: 800,
                    backgroundColor: riskStatus.circuit_breaker_state === 'armed' ? 'rgba(16, 185, 129, 0.2)' : 'rgba(239, 68, 68, 0.2)',
                    color: riskStatus.circuit_breaker_state === 'armed' ? '#10b981' : '#ef4444',
                  }}
                >
                  {riskStatus.circuit_breaker_state === 'armed' ? '🟢 CORTAFUEGOS ACTIVO Y SEGURO' : '🔴 CORTAFUEGOS DISPARADO'}
                </span>
              </div>
              <div className="kpi-grid">
                <div className="kpi-card" style={{ padding: '12px' }}>
                  <span className="kpi-label">Pérdida Diaria Actual / Límite</span>
                  <span className="kpi-value">
                    {safeFix(riskStatus.daily_loss_pct)}% / {safeFix(riskStatus.max_daily_loss_pct)}%
                  </span>
                </div>
                <div className="kpi-card" style={{ padding: '12px' }}>
                  <span className="kpi-label">Drawdown Actual / Límite</span>
                  <span className="kpi-value">
                    {safeFix(riskStatus.current_drawdown_pct)}% / {safeFix(riskStatus.max_drawdown_pct)}%
                  </span>
                </div>
                <div className="kpi-card" style={{ padding: '12px' }}>
                  <span className="kpi-label">Posiciones Abiertas / Máx.</span>
                  <span className="kpi-value">
                    {safeNum(riskStatus.open_positions_count)} / {safeNum(riskStatus.max_open_positions)}
                  </span>
                </div>
                <div className="kpi-card" style={{ padding: '12px' }}>
                  <span className="kpi-label">Exposición Bruta / Límite</span>
                  <span className="kpi-value">
                    {safeLoc(riskStatus.gross_exposure_usd, { maximumFractionDigits: 2 })} USDT
                    {safeNum(riskStatus.current_equity_usd) > 0 && (
                      <> ({safeFix((safeNum(riskStatus.gross_exposure_usd) / safeNum(riskStatus.current_equity_usd)) * 100)}%)</>
                    )}
                    {' / '}{safeFix(riskStatus.max_portfolio_exposure_pct, 0)}%
                  </span>
                </div>
              </div>

              {concentrationReport && (
                <div style={{ marginTop: '18px', borderTop: '1px solid rgba(148, 163, 184, 0.2)', paddingTop: '16px' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '12px', flexWrap: 'wrap' }}>
                    <div>
                      <h4 style={{ margin: 0, fontSize: '14px', fontWeight: 800 }}>
                        🧩 Concentración y Correlación{' '}
                        <span style={{ fontSize: '11px', fontWeight: 700, color: '#94a3b8' }}>solo lectura</span>
                      </h4>
                      <p style={{ margin: '4px 0 0', fontSize: '12px', color: '#94a3b8' }}>
                        Exposición por activo y correlación de retornos {concentrationReport.timeframe}. No bloquea ni dimensiona órdenes.
                      </p>
                    </div>
                    <button
                      type="button"
                      className="btn btn-secondary"
                      style={{ fontSize: '11px', padding: '5px 10px' }}
                      onClick={refreshConcentration}
                    >
                      Actualizar
                    </button>
                  </div>

                  <div className="kpi-grid" style={{ marginTop: '12px' }}>
                    <div className="kpi-card" style={{ padding: '12px' }}>
                      <span className="kpi-label">Capital</span>
                      <span className="kpi-value">{safeLoc(concentrationReport.equity_usd, { maximumFractionDigits: 2 })} USDT</span>
                    </div>
                    <div className="kpi-card" style={{ padding: '12px' }}>
                      <span className="kpi-label">Exposición bruta</span>
                      <span className="kpi-value">
                        {safeLoc(concentrationReport.gross_exposure_usd, { maximumFractionDigits: 2 })} USDT
                        {concentrationReport.gross_exposure_pct != null && (
                          <> ({safeFix(concentrationReport.gross_exposure_pct)}%)</>
                        )}
                      </span>
                    </div>
                    <div className="kpi-card" style={{ padding: '12px' }}>
                      <span className="kpi-label">Límite por activo</span>
                      <span className="kpi-value">{safeFix(concentrationReport.max_position_size_pct, 0)}%</span>
                    </div>
                    <div className="kpi-card" style={{ padding: '12px' }}>
                      <span className="kpi-label">Correlación más alta</span>
                      <span className="kpi-value">
                        {(() => {
                          const values = concentrationReport.correlations
                            .map((pair) => pair.correlation)
                            .filter((value): value is number => value != null);
                          return values.length > 0
                            ? safeFix(values.reduce((best, value) => (Math.abs(value) > Math.abs(best) ? value : best), 0))
                            : 'n/d';
                        })()}
                      </span>
                    </div>
                  </div>

                  {concentrationReport.positions.length > 0 ? (
                    <div style={{ marginTop: '12px', display: 'flex', flexDirection: 'column', gap: '6px' }}>
                      {concentrationReport.positions.map((position) => (
                        <div
                          key={position.symbol}
                          style={{
                            display: 'flex',
                            justifyContent: 'space-between',
                            gap: '10px',
                            flexWrap: 'wrap',
                            padding: '8px 10px',
                            borderRadius: '6px',
                            backgroundColor: position.within_limit === false ? 'rgba(239, 68, 68, 0.08)' : 'rgba(148, 163, 184, 0.06)',
                            fontSize: '12px',
                          }}
                        >
                          <strong style={{ color: position.within_limit === false ? '#ef4444' : '#e2e8f0' }}>
                            {position.symbol}
                          </strong>
                          <span>
                            {safeLoc(position.exposure_usd, { maximumFractionDigits: 2 })} USDT
                            {position.equity_pct != null && <> · {safeFix(position.equity_pct)}% del capital</>}
                            {position.limit_usd != null && <> · límite {safeLoc(position.limit_usd, { maximumFractionDigits: 2 })} USDT</>}
                          </span>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p style={{ margin: '12px 0 0', fontSize: '12px', color: '#94a3b8' }}>Sin posiciones abiertas.</p>
                  )}

                  {concentrationReport.correlations.length > 0 && (
                    <div style={{ marginTop: '10px', display: 'flex', flexDirection: 'column', gap: '4px', fontSize: '12px' }}>
                      {concentrationReport.correlations.map((pair) => (
                        <span key={`${pair.left}-${pair.right}`} style={{ color: pair.high ? '#f59e0b' : '#94a3b8' }}>
                          {pair.left} ↔ {pair.right}:{' '}
                          <strong>{pair.correlation == null ? 'n/d' : safeFix(pair.correlation, 3)}</strong>
                          {' '}({pair.sample_size} velas){pair.high ? ' · CORRELACIÓN ALTA' : ''}
                        </span>
                      ))}
                    </div>
                  )}

                  {concentrationReport.warnings.length > 0 && (
                    <ul style={{ margin: '10px 0 0', paddingLeft: '18px', fontSize: '12px', color: '#f59e0b' }}>
                      {concentrationReport.warnings.map((warning) => (
                        <li key={warning}>{warning}</li>
                      ))}
                    </ul>
                  )}

                  <p style={{ margin: '8px 0 0', fontSize: '11px', opacity: 0.55 }}>
                    {concentrationReport.disclaimer}
                  </p>
                </div>
              )}
            </div>
          )}

          {/* Drift de rendimiento (solo lectura) */}
          {driftReport && (
            <div className="panel" style={{ padding: '24px' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '14px' }}>
                <h3 style={{ margin: 0, fontSize: '16px', fontWeight: 800 }}>
                  📉 Drift de Rendimiento (solo lectura)
                </h3>
                <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                  <span
                    style={{
                      padding: '3px 10px',
                      borderRadius: '6px',
                      fontSize: '11px',
                      fontWeight: 800,
                      backgroundColor:
                        driftReport.status === 'degraded'
                          ? 'rgba(239, 68, 68, 0.2)'
                          : driftReport.status === 'watch'
                            ? 'rgba(245, 158, 11, 0.2)'
                            : driftReport.status === 'stable'
                              ? 'rgba(16, 185, 129, 0.2)'
                              : 'rgba(148, 163, 184, 0.2)',
                      color:
                        driftReport.status === 'degraded'
                          ? '#ef4444'
                          : driftReport.status === 'watch'
                            ? '#f59e0b'
                            : driftReport.status === 'stable'
                              ? '#10b981'
                              : '#94a3b8',
                    }}
                  >
                    {driftReport.status === 'degraded'
                      ? 'DEGRADADO'
                      : driftReport.status === 'watch'
                        ? 'VIGILAR'
                        : driftReport.status === 'stable'
                          ? 'ESTABLE'
                          : 'DATOS INSUFICIENTES'}
                  </span>
                  <button
                    onClick={refreshDrift}
                    style={{
                      padding: '4px 10px',
                      borderRadius: '6px',
                      border: '1px solid rgba(255,255,255,0.15)',
                      background: 'transparent',
                      color: 'inherit',
                      fontSize: '11px',
                      cursor: 'pointer',
                    }}
                  >
                    Actualizar
                  </button>
                </div>
              </div>
              <div className="kpi-grid">
                <div className="kpi-card" style={{ padding: '12px' }}>
                  <span className="kpi-label">Muestra reciente / base</span>
                  <span className="kpi-value">
                    {safeNum(driftReport.recent.sample_size)} / {safeNum(driftReport.baseline.sample_size)}
                  </span>
                </div>
                <div className="kpi-card" style={{ padding: '12px' }}>
                  <span className="kpi-label">Acierto reciente / base</span>
                  <span className="kpi-value">
                    {safeMetric(driftReport.recent.win_rate_pct)}% / {safeMetric(driftReport.baseline.win_rate_pct)}%
                  </span>
                </div>
                <div className="kpi-card" style={{ padding: '12px' }}>
                  <span className="kpi-label">Retorno neto medio reciente / base</span>
                  <span className="kpi-value">
                    {safeMetric(driftReport.recent.avg_net_pnl_pct, 4)}% / {safeMetric(driftReport.baseline.avg_net_pnl_pct, 4)}%
                  </span>
                </div>
                <div className="kpi-card" style={{ padding: '12px' }}>
                  <span className="kpi-label">Profit factor reciente / base</span>
                  <span className="kpi-value">
                    {safeMetric(driftReport.recent.profit_factor)} / {safeMetric(driftReport.baseline.profit_factor)}
                  </span>
                </div>
              </div>
              <p style={{ margin: '12px 0 0', fontSize: '12px', opacity: 0.8 }}>
                {driftReport.reasons.join(' · ')}
              </p>
              <p style={{ margin: '6px 0 0', fontSize: '11px', opacity: 0.55 }}>
                {driftReport.disclaimer} No suspende trading ni ajusta el tamaño de las posiciones.
              </p>
            </div>
          )}

          {/* Oportunidades en sombra y coste de IA (solo lectura) */}
          <div className="panel" style={{ padding: '24px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '14px' }}>
              <h3 style={{ margin: 0, fontSize: '16px', fontWeight: 800 }}>
                🔎 Oportunidades en Sombra y Coste de IA
              </h3>
              <span
                style={{
                  padding: '3px 10px',
                  borderRadius: '6px',
                  fontSize: '11px',
                  fontWeight: 800,
                  backgroundColor: 'rgba(59, 130, 246, 0.2)',
                  color: '#3b82f6',
                }}
              >
                SOLO LECTURA · SIN EJECUCIÓN
              </span>
            </div>

            {!latestShadowScan ? (
              <p style={{ margin: 0, fontSize: '13px', opacity: 0.75 }}>
                Todavía no hay escaneos shadow persistidos. El escáner no envía órdenes ni llama al LLM.
              </p>
            ) : (
              <>
                <div className="kpi-grid" style={{ marginBottom: '14px' }}>
                  <div className="kpi-card" style={{ padding: '12px' }}>
                    <span className="kpi-label">Decisión de política</span>
                    <span
                      className="kpi-value"
                      style={{
                        fontSize: '14px',
                        color: opportunityAssessment?.decision === 'eligible_for_manual_review' ? '#f59e0b' : '#10b981',
                      }}
                    >
                      {opportunityAssessment
                        ? opportunityAssessment.decision === 'eligible_for_manual_review'
                          ? 'REVISIÓN MANUAL'
                          : 'ABSTENERSE'
                        : 'SIN EVALUAR'}
                    </span>
                  </div>
                  <div className="kpi-card" style={{ padding: '12px' }}>
                    <span className="kpi-label">Candidato señalado</span>
                    <span className="kpi-value" style={{ fontSize: '14px' }}>
                      {opportunityAssessment?.selected_symbol || 'Ninguno'}
                    </span>
                  </div>
                  <div className="kpi-card" style={{ padding: '12px' }}>
                    <span className="kpi-label">Evaluaciones maduras</span>
                    <span className="kpi-value" style={{ fontSize: '14px' }}>
                      {safeNum(opportunityAssessment?.evidence?.matured_evaluations)}
                    </span>
                  </div>
                  <div className="kpi-card" style={{ padding: '12px' }}>
                    <span className="kpi-label">Evidencia neta media</span>
                    <span className="kpi-value" style={{ fontSize: '14px' }}>
                      {opportunityAssessment?.evidence?.mean_top_minus_alternatives_net_pct == null
                        ? 'Sin datos'
                        : `${safeFix(opportunityAssessment.evidence.mean_top_minus_alternatives_net_pct, 4)} pp`}
                    </span>
                  </div>
                </div>

                <div style={{ overflowX: 'auto', marginBottom: '12px' }}>
                  <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12px' }}>
                    <thead>
                      <tr style={{ textAlign: 'left', opacity: 0.7 }}>
                        <th style={{ padding: '6px 8px' }}>#</th>
                        <th style={{ padding: '6px 8px' }}>Símbolo</th>
                        <th style={{ padding: '6px 8px' }}>Movimiento</th>
                        <th style={{ padding: '6px 8px' }}>Spread</th>
                        <th style={{ padding: '6px 8px' }}>Régimen</th>
                        <th style={{ padding: '6px 8px' }}>Neto tras coste</th>
                        <th style={{ padding: '6px 8px' }}>Elegible</th>
                      </tr>
                    </thead>
                    <tbody>
                      {latestShadowScan.candidates.map((candidate) => {
                        const assessed = opportunityAssessment?.candidates.find(
                          (item) => item.symbol === candidate.symbol
                        );
                        return (
                          <tr key={candidate.symbol} style={{ borderTop: '1px solid rgba(255,255,255,0.08)' }}>
                            <td style={{ padding: '6px 8px' }}>{candidate.rank ?? '-'}</td>
                            <td style={{ padding: '6px 8px', fontWeight: 700 }}>{candidate.symbol}</td>
                            <td style={{ padding: '6px 8px' }}>
                              {candidate.observed_movement_less_spread_pct == null
                                ? 'Sin datos'
                                : `${safeFix(candidate.observed_movement_less_spread_pct, 4)}%`}
                            </td>
                            <td style={{ padding: '6px 8px' }}>
                              {candidate.quoted_spread_pct == null
                                ? 'No disponible'
                                : `${safeFix(candidate.quoted_spread_pct, 4)}%`}
                            </td>
                            <td style={{ padding: '6px 8px' }}>
                              {candidate.regime_status === 'classified' && candidate.trend_regime
                                ? `${candidate.trend_regime} / ${candidate.volatility_regime ?? '-'}`
                                : 'Sin clasificar'}
                            </td>
                            <td style={{ padding: '6px 8px' }}>
                              {assessed?.cost_adjusted_movement_pct == null
                                ? 'Sin datos'
                                : `${safeFix(assessed.cost_adjusted_movement_pct, 4)}%`}
                            </td>
                            <td style={{ padding: '6px 8px', color: assessed?.eligible ? '#10b981' : '#f59e0b' }}>
                              {assessed ? (assessed.eligible ? 'Sí' : 'No') : '-'}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>

                <p style={{ margin: '0 0 10px', fontSize: '12px', opacity: 0.8 }}>
                  {opportunityAssessment?.selection_reason ||
                    'La política se abstiene por defecto hasta que existan datos frescos, spread acotado y evidencia neta madura.'}
                </p>
                <p style={{ margin: 0, fontSize: '11px', opacity: 0.6 }}>
                  {opportunityAssessment?.evidence?.interpretation ||
                    'Movimiento de precio descriptivo; no es P&L realizado ni probabilidad de beneficio.'}
                </p>
              </>
            )}

            <div style={{ marginTop: '14px' }}>
              <h4 style={{ margin: '0 0 8px', fontSize: '13px', fontWeight: 800 }}>
                🧠 Memoria de investigación vigente ({selectedSymbol})
              </h4>
              {!researchContext || researchContext.notes.length === 0 ? (
                <p style={{ margin: 0, fontSize: '12px', opacity: 0.7 }}>
                  Sin notas vigentes para este ámbito. La memoria no altera señales ni riesgo.
                </p>
              ) : (
                <ul style={{ margin: 0, paddingLeft: '18px', fontSize: '12px', lineHeight: 1.5 }}>
                  {researchContext.notes.map((note) => (
                    <li key={note.id}>
                      <strong>{note.title}</strong>{' '}
                      <span style={{ opacity: 0.7 }}>
                        [{note.kind} · {note.validation_status}
                        {note.timeframe ? ` · ${note.timeframe}` : ''}]
                      </span>
                      <div style={{ opacity: 0.8 }}>{note.statement}</div>
                    </li>
                  ))}
                </ul>
              )}
              <p style={{ margin: '6px 0 0', fontSize: '11px', opacity: 0.55 }}>
                {researchContext?.disclaimer ||
                  'Contexto de solo lectura: no es una señal, probabilidad ni autorización para operar.'}{' '}
                Solo se envía a la IA si el operador activa el opt-in y la nota está validada como
                «supported».
              </p>
            </div>

            <div
              style={{
                marginTop: '16px',
                paddingTop: '12px',
                borderTop: '1px solid rgba(255,255,255,0.08)',
                display: 'flex',
                gap: '24px',
                flexWrap: 'wrap',
                fontSize: '12px',
              }}
            >
              <span>
                <strong>IA:</strong>{' '}
                {llmStatus?.mode === 'llm_with_fallback'
                  ? `${llmStatus.primary_provider ?? 'n/d'} (fallback: ${(llmStatus.fallback_providers || []).join(', ') || 'ninguno'})`
                  : 'Fallback determinista'}
              </span>
              <span>
                <strong>Tokens hoy:</strong>{' '}
                {safeNum(llmStatus?.usage?.tokens_used)}
                {safeNum(llmStatus?.usage?.daily_token_budget) > 0
                  ? ` / ${safeNum(llmStatus?.usage?.daily_token_budget)}`
                  : ' (sin tope)'}
              </span>
              <span>
                <strong>Tokens ciclo:</strong>{' '}
                {safeNum(llmStatus?.usage?.cycle_tokens_used)}
                {safeNum(llmStatus?.usage?.cycle_token_budget) > 0
                  ? ` / ${safeNum(llmStatus?.usage?.cycle_token_budget)}`
                  : ' (sin tope)'}
              </span>
              <span>
                <strong>Llamadas OK:</strong> {safeNum(llmStatus?.usage?.requests)}
              </span>
              <span>
                <strong>Peticiones hoy:</strong>{' '}
                {safeNum(llmStatus?.usage?.request_attempts)}
                {safeNum(llmStatus?.usage?.daily_request_budget) > 0
                  ? ` / ${safeNum(llmStatus?.usage?.daily_request_budget)}`
                  : ' (sin tope)'}
              </span>
              <span>
                <strong>Peticiones ciclo:</strong>{' '}
                {safeNum(llmStatus?.usage?.cycle_requests_used)}
                {safeNum(llmStatus?.usage?.cycle_request_budget) > 0
                  ? ` / ${safeNum(llmStatus?.usage?.cycle_request_budget)}`
                  : ' (sin tope)'}
              </span>
              <span>
                <strong>Fallbacks:</strong> {safeNum(llmStatus?.usage?.fallbacks)}
              </span>
              {(llmStatus?.usage?.provider_cooldowns?.length ?? 0) > 0 && (
                <span style={{ color: '#f59e0b' }}>
                  <strong>Proveedores en espera:</strong>{' '}
                  {llmStatus?.usage?.provider_cooldowns
                    ?.map(
                      (cooldown) =>
                        `${cooldown.provider} (${cooldown.reason}, ${Math.ceil(
                          cooldown.seconds_remaining / 60,
                        )} min)`,
                    )
                    .join(', ')}
                </span>
              )}
              <span>
                <strong>Coste IA est.:</strong>{' '}
                {llmStatus?.usage?.pricing?.configured
                  ? safeUsd(llmStatus?.usage?.estimated_cost_usd)
                  : 'tarifas sin configurar'}
              </span>
              <span style={{ color: (llmStatus?.usage?.budget_exhausted || llmStatus?.usage?.cycle_budget_exhausted || llmStatus?.usage?.request_budget_exhausted || llmStatus?.usage?.cycle_request_budget_exhausted) ? '#ef4444' : '#10b981' }}>
                {(llmStatus?.usage?.budget_exhausted || llmStatus?.usage?.cycle_budget_exhausted || llmStatus?.usage?.request_budget_exhausted || llmStatus?.usage?.cycle_request_budget_exhausted)
                  ? 'Presupuesto agotado'
                  : 'Presupuesto disponible'}
              </span>
            </div>
            {Object.keys(llmStatus?.usage?.by_agent ?? {}).length > 0 && (
              <div
                style={{
                  marginTop: '10px',
                  display: 'flex',
                  gap: '16px',
                  flexWrap: 'wrap',
                  fontSize: '11px',
                  color: '#94a3b8',
                }}
              >
                {Object.entries(llmStatus?.usage?.by_agent ?? {}).map(([agent, bucket]) => (
                  <span key={agent}>
                    <strong>{agent}</strong>: {safeNum(bucket.total_tokens)} tok
                    {' · '}
                    {safeNum(bucket.requests)} llamadas
                    {' · '}
                    {bucket.estimated_cost_usd == null
                      ? 'coste n/d'
                      : safeUsd(bucket.estimated_cost_usd)}
                    {safeNum(bucket.failures) > 0 ? ` · ${safeNum(bucket.failures)} fallos` : ''}
                  </span>
                ))}
              </div>
            )}
          </div>

          {/* Salud de los Servicios del Sistema */}
          <div className="panel" style={{ padding: '24px' }}>
            <h3 style={{ margin: '0 0 14px', fontSize: '16px', fontWeight: 800 }}>
              🛠️ Estado de los Componentes del Sistema
            </h3>
            <div className="status-grid" style={{ marginBottom: 0 }}>
              {Object.entries(status).map(([service, state]) => (
                <div key={service} className="status-card" style={{ padding: '12px' }}>
                  <div className="status-header">
                    <span className="service-name" style={{ fontSize: '13px' }}>
                      {service === 'database'
                        ? 'Base de Datos'
                        : service === 'redis'
                        ? 'Caché Redis'
                        : service === 'market_data'
                        ? 'Datos de Mercado'
                        : service === 'trading_engine'
                        ? 'Motor de Órdenes'
                        : service}
                    </span>
                    <span className={`status-indicator ${state}`} />
                  </div>
                  <span className="status-value" style={{ fontSize: '14px', color: state === 'healthy' || state === 'ok' ? '#10b981' : '#f59e0b' }}>
                    {state === 'healthy' || state === 'ok' ? 'EN LÍNEA' : state.toUpperCase()}
                  </span>
                </div>
              ))}
            </div>
          </div>
            </div>
          </details>
        </div>
      )}
    </div>
  );
}
