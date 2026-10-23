import { useState, useEffect, useMemo } from 'react';
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

export interface RiskStatusSummary {
  circuit_breaker_state: string;
  portfolio_daily_pnl_pct: number;
  portfolio_drawdown_pct: number;
  open_positions_count: number;
  max_allowed_positions: number;
  can_open_new_position: boolean;
  reject_reasons: string[];
}

interface PositionData {
  id: string;
  symbol: string;
  side: string;
  quantity: number;
  entry_price: number;
  current_price: number;
  unrealized_pnl: number;
  unrealized_pnl_pct: number;
  stop_loss?: number;
  take_profit?: number;
  opened_at: string;
}

interface PortfolioData {
  cash: number;
  total_balance: number;
  available_balance: number;
  margin_used: number;
  total_unrealized_pnl: number;
  total_realized_pnl: number;
  position_count: number;
  positions: PositionData[];
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
  timeframe: string;
  interval_seconds: number;
  execution_mode: string;
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
  last_price?: number | null;
  last_signal?: string | null;
  last_confidence?: number | null;
  last_error?: string | null;
  active_positions_count: number;
  logs: AutonomousLogEntry[];
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

type DashboardTab = 'bot' | 'market' | 'portfolio' | 'agents' | 'settings';

export default function App() {
  const [activeTab, setActiveTab] = useState<DashboardTab>('bot');
  const [status, setStatus] = useState<Record<string, string>>({});

  // Mercado y WebSockets
  const [selectedSymbol, setSelectedSymbol] = useState<string>('BTC/USDT');
  const [selectedTimeframe, setSelectedTimeframe] = useState<string>('1h');
  const [candles, setCandles] = useState<CandleItem[]>([]);
  const [isLoadingCandles, setIsLoadingCandles] = useState<boolean>(false);
  const [isSyncing, setIsSyncing] = useState<boolean>(false);
  const [wsConnected, setWsConnected] = useState<boolean>(false);
  const [livePrice, setLivePrice] = useState<number | null>(null);
  const [lastTickTime, setLastTickTime] = useState<string | null>(null);

  // Cartera y Operaciones
  const [portfolio, setPortfolio] = useState<PortfolioData | null>(null);
  const [executionMode, setExecutionMode] = useState<'PAPER' | 'REAL'>('PAPER');
  const [orderAmountUsd, setOrderAmountUsd] = useState<number>(500);
  const [isSubmittingOrder, setIsSubmittingOrder] = useState<boolean>(false);

  // Bot Autónomo
  const [autonomousStatus, setAutonomousStatus] = useState<AutonomousBotStatus | null>(null);
  const [botInterval, setBotInterval] = useState<number>(5);
  const [botRiskLevel, setBotRiskLevel] = useState<'conservative' | 'moderate' | 'aggressive'>('moderate');
  const [botAutoExecute, setBotAutoExecute] = useState<boolean>(true);
  const [isTogglingBot, setIsTogglingBot] = useState<boolean>(false);

  // Análisis Multi-Agente
  const [aiDecision, setAiDecision] = useState<CoordinatorDecisionData | null>(null);
  const [isAnalyzingAI, setIsAnalyzingAI] = useState<boolean>(false);

  // Riesgo y Seguridad
  const [riskStatus, setRiskStatus] = useState<RiskStatusSummary | null>(null);

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
  const [alertStatus, setAlertStatus] = useState<AlertStatusData | null>(null);
  const [isTestingAlert, setIsTestingAlert] = useState<boolean>(false);
  const [isReconcilingReal, setIsReconcilingReal] = useState<boolean>(false);

  // Refrescos periódicos
  const refreshPortfolio = () => {
    fetch('/api/v1/portfolio')
      .then((res) => res.json())
      .then((data: PortfolioData) => setPortfolio(data))
      .catch((err) => console.error('Error al consultar cartera', err));
  };

  const refreshAutonomousStatus = () => {
    fetch('/api/v1/autonomous/status')
      .then((res) => res.json())
      .then((data: AutonomousBotStatus) => {
        setAutonomousStatus(data);
        if (data && data.config) {
          setBotInterval(data.config.interval_seconds);
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

  const refreshRealTrading = () => {
    fetch('/api/v1/trading/real/status')
      .then((res) => (res.ok ? res.json() : null))
      .then((data: RealTradingStatusData | null) => setRealTradingStatus(data))
      .catch((err) => console.error('Error estado trading real', err));
  };

  const refreshAlerts = () => {
    fetch('/api/v1/alerts/status')
      .then((res) => (res.ok ? res.json() : null))
      .then((data: AlertStatusData | null) => setAlertStatus(data))
      .catch((err) => console.error('Error estado alertas', err));
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

    refreshPortfolio();
    refreshAutonomousStatus();
    refreshVault();
    refreshRisk();
    refreshRealTrading();
    refreshAlerts();

    const intervalTimer = setInterval(() => {
      refreshAutonomousStatus();
      refreshPortfolio();
    }, 3000);

    return () => clearInterval(intervalTimer);
  }, []);

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
            if (data.timestamp) setLastTickTime(data.timestamp);
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
  }, [selectedSymbol, selectedTimeframe]);

  // Cálculo de estadísticas rápidas
  const marketStats = useMemo(() => {
    if (!candles.length) return null;
    const latest = candles[candles.length - 1];
    const prev = candles.length > 1 ? candles[candles.length - 2] : latest;
    const currentP = livePrice ?? latest.close;
    const change = prev.close > 0 ? ((currentP - prev.close) / prev.close) * 100 : 0;
    const high24h = Math.max(...candles.map((c) => c.high));
    const low24h = Math.min(...candles.map((c) => c.low));
    const volumeTotal = candles.reduce((acc, c) => acc + c.volume, 0);

    return {
      currentPrice: currentP,
      change,
      high24h,
      low24h,
      volumeTotal,
    };
  }, [candles, livePrice]);

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
      const payload: AutonomousBotConfig = {
        symbol: selectedSymbol,
        timeframe: selectedTimeframe,
        interval_seconds: botInterval,
        execution_mode: executionMode.toLowerCase(),
        min_confidence_pct: 50,
        risk_per_trade_pct: riskPct,
        max_open_positions: 3,
        auto_execute: botAutoExecute,
        stop_loss_pct: 2.0,
        take_profit_pct: 4.0,
      };

      fetch('/api/v1/autonomous/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
        .then((res) => {
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          return res.json();
        })
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
        refreshPortfolio();
      })
      .catch((err) => toast.error(`Error de parada: ${err.message}`));
  };

  // Sincronizar velas
  const handleSyncMarket = () => {
    setIsSyncing(true);
    const encoded = encodeURIComponent(selectedSymbol);
    fetch(`/api/v1/market/${encoded}/sync?timeframe=${selectedTimeframe}&days_back=14`, {
      method: 'POST',
    })
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then((data) => {
        toast.success(`✅ ${data.candles_synced} velas sincronizadas correctamente.`);
        // Recargar velas
        fetch(`/api/v1/market/${encoded}/candles?timeframe=${selectedTimeframe}&limit=50`)
          .then((r) => r.json())
          .then((d) => setCandles(d.data || []));
      })
      .catch((err) => toast.error(`Error al sincronizar: ${err.message}`))
      .finally(() => setIsSyncing(false));
  };

  // Operación rápida manual (Compra / Venta con 1 clic)
  const handleQuickTrade = (side: 'BUY' | 'SELL') => {
    const currentP = livePrice || (candles.length ? candles[candles.length - 1].close : 84800);
    const qty = Number((orderAmountUsd / currentP).toFixed(4));

    if (qty <= 0) {
      toast.error('El monto debe ser mayor a 0');
      return;
    }

    const isReal = executionMode === 'REAL';
    if (isReal) {
      if (!realTradingStatus?.enabled || !realTradingStatus.credentials_configured) {
        toast.error(
          'Trading real no habilitado. Configura EXECUTION_MODE=real, ENABLE_REAL_TRADING=true y guarda tus claves API.'
        );
        return;
      }
      const venue = realTradingStatus.testnet ? 'TESTNET (fondos de prueba)' : 'MAINNET (DINERO REAL)';
      if (!window.confirm(`¿Confirmas ${side} de ${qty} ${selectedSymbol.split('/')[0]} en ${venue}?`)) {
        return;
      }
    }

    setIsSubmittingOrder(true);
    const payload = {
      symbol: selectedSymbol,
      side,
      order_type: 'MARKET',
      quantity: qty,
      mode: executionMode.toLowerCase(),
      ...(isReal
        ? {
            exchange: realTradingStatus?.exchange ?? 'binance',
            confirm: true,
            client_order_id: `web-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`,
            stop_loss: side === 'BUY' ? Number((currentP * 0.98).toFixed(2)) : undefined,
            take_profit: side === 'BUY' ? Number((currentP * 1.04).toFixed(2)) : undefined,
          }
        : {}),
      notes: `Operación Rápida desde Panel en Español`,
    };

    const endpoint = isReal
      ? side === 'BUY'
        ? '/api/v1/trading/real/orders'
        : '/api/v1/trading/real/positions/close'
      : '/api/v1/trading/orders';

    fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
      .then((res) => {
        if (!res.ok) return res.json().then((e) => { throw new Error(e.detail || `HTTP ${res.status}`); });
        return res.json();
      })
      .then((order) => {
        const accionStr = side === 'BUY' ? 'COMPRA' : 'VENTA';
        toast.success(`✅ ¡Orden de ${accionStr} ejecutada! ${qty} ${selectedSymbol.split('/')[0]} @ $${order.average_fill_price}`);
        refreshPortfolio();
      })
      .catch((err) => toast.error(`Error en la orden: ${err.message}`))
      .finally(() => setIsSubmittingOrder(false));
  };

  const handleClosePosition = (positionId: string) => {
    fetch(`/api/v1/trading/positions/${positionId}/close`, { method: 'POST' })
      .then((res) => {
        if (!res.ok) return res.json().then((e) => { throw new Error(e.detail || `HTTP ${res.status}`); });
        return res.json();
      })
      .then((order) => {
        toast.success(`Posición cerrada exitosamente @ $${order.average_fill_price}`);
        refreshPortfolio();
      })
      .catch((err) => toast.error(`Error al cerrar: ${err.message}`));
  };

  const handleResetPaperAccount = () => {
    if (!window.confirm('¿Deseas reiniciar el saldo virtual a $10,000.00 USDT?')) return;
    fetch('/api/v1/portfolio/reset', { method: 'POST' })
      .then((res) => res.json())
      .then(() => {
        toast.success('Cuenta virtual reiniciada a $10,000.00 USDT');
        refreshPortfolio();
      })
      .catch((err) => toast.error(`Error al reiniciar: ${err.message}`));
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
        toast.success(`Veredicto del Comité: ${signalTraducida} (${Math.round(safeNum(data.aggregate_confidence) * 100)}% de confianza)`);
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
      body: JSON.stringify({ exchange: 'binance' }),
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

        {/* Controles de Modo y Estado en Vivo */}
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

          {/* Selector de Modo Simulado / Real */}
          <button
            type="button"
            className={`btn ${executionMode === 'PAPER' ? 'btn-secondary' : 'btn-danger'}`}
            style={{ fontSize: '13px', padding: '6px 14px' }}
            onClick={() => {
              const nextMode = executionMode === 'PAPER' ? 'REAL' : 'PAPER';
              if (
                nextMode === 'REAL' &&
                !window.confirm(
                  '🚨 ¿Deseas activar el MODO REAL? Asegúrate de tener tus claves API configuradas en la pestaña Configuración.'
                )
              ) {
                return;
              }
              setExecutionMode(nextMode);
              toast(
                nextMode === 'PAPER'
                  ? '🛡️ Modo Simulado Activado (Saldo de prueba)'
                  : '🚨 Modo Real Activado',
                { icon: nextMode === 'PAPER' ? '🛡️' : '⚠️' }
              );
            }}
          >
            {executionMode === 'PAPER' ? '🛡️ MODO SIMULADO (SEGURO)' : '🚨 MODO DINERO REAL'}
          </button>
        </div>
      </header>

      {/* ====================================================================
          RESUMEN RÁPIDO DE CARTERA (BANNER SUPERIOR)
          ==================================================================== */}
      {portfolio && (
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
            gap: '12px',
            marginBottom: '20px',
            padding: '14px 20px',
            background: 'rgba(15, 23, 42, 0.7)',
            borderRadius: '14px',
            border: '1px solid rgba(255, 255, 255, 0.06)',
            alignItems: 'center',
          }}
        >
          <div>
            <span style={{ fontSize: '11px', color: '#94a3b8', textTransform: 'uppercase', fontWeight: 600 }}>
              Saldo de la Cuenta
            </span>
            <div style={{ fontSize: '20px', fontWeight: 800, color: '#f8fafc' }}>
              ${safeLoc(portfolio.total_balance, { minimumFractionDigits: 2 })} USDT
            </div>
          </div>

          <div>
            <span style={{ fontSize: '11px', color: '#94a3b8', textTransform: 'uppercase', fontWeight: 600 }}>
              Ganancia / Pérdida en Vivo
            </span>
            <div
              style={{
                fontSize: '18px',
                fontWeight: 800,
                color: safeNum(portfolio.total_unrealized_pnl) >= 0 ? '#10b981' : '#ef4444',
              }}
            >
              {safeNum(portfolio.total_unrealized_pnl) >= 0 ? '+' : ''}$
              {safeFix(portfolio.total_unrealized_pnl)} USDT
            </div>
          </div>

          <div>
            <span style={{ fontSize: '11px', color: '#94a3b8', textTransform: 'uppercase', fontWeight: 600 }}>
              Operaciones Abiertas
            </span>
            <div style={{ fontSize: '18px', fontWeight: 800, color: '#38bdf8' }}>
              {portfolio.position_count} {portfolio.position_count === 1 ? 'activa' : 'activas'}
            </div>
          </div>

          {/* Selector de Cripto Activa */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
            <span style={{ fontSize: '11px', color: '#94a3b8', textTransform: 'uppercase', fontWeight: 600 }}>
              Criptomoneda en Foco
            </span>
            <select
              className="select-input"
              style={{ padding: '6px 12px', fontSize: '13px', fontWeight: 700 }}
              value={selectedSymbol}
              onChange={(e) => setSelectedSymbol(e.target.value)}
            >
              {AVAILABLE_SYMBOLS.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.icon} {s.name}
                </option>
              ))}
            </select>
          </div>
        </div>
      )}

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
          className={`nav-tab-btn ${activeTab === 'market' ? 'active' : ''}`}
          onClick={() => setActiveTab('market')}
        >
          📈 Mercado & Gráfico
        </button>

        <button
          type="button"
          className={`nav-tab-btn ${activeTab === 'portfolio' ? 'active' : ''}`}
          onClick={() => setActiveTab('portfolio')}
        >
          💼 Cartera & Operaciones
        </button>

        <button
          type="button"
          className={`nav-tab-btn ${activeTab === 'agents' ? 'active' : ''}`}
          onClick={() => setActiveTab('agents')}
        >
          🧠 Comité de Analistas IA
        </button>

        <button
          type="button"
          className={`nav-tab-btn ${activeTab === 'settings' ? 'active' : ''}`}
          onClick={() => setActiveTab('settings')}
        >
          ⚙️ Configuración & Claves
        </button>
      </nav>

      {/* ====================================================================
          PESTAÑA 1: ASISTENTE AUTÓNOMO (CENTRO PRINCIPAL)
          ==================================================================== */}
      {activeTab === 'bot' && (
        <div>
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
                    Par: <strong>{selectedSymbol}</strong> • Escaneo cada {botInterval}s
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

                {/* Métricas clave en tarjetas pequeñas */}
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '12px', marginTop: '16px' }}>
                  <div className="kpi-card" style={{ padding: '12px' }}>
                    <span className="kpi-label">Precio Evaluado</span>
                    <span className="kpi-value" style={{ fontSize: '16px', color: '#38bdf8' }}>
                      ${safeLoc(autonomousStatus?.last_price || livePrice || 0, { minimumFractionDigits: 2 })}
                    </span>
                  </div>
                  <div className="kpi-card" style={{ padding: '12px' }}>
                    <span className="kpi-label">Ciclos Completados</span>
                    <span className="kpi-value" style={{ fontSize: '16px' }}>
                      {autonomousStatus?.total_cycles || 0}
                    </span>
                  </div>
                  <div className="kpi-card" style={{ padding: '12px' }}>
                    <span className="kpi-label">Órdenes Realizadas</span>
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
                  Controles del Asistente
                </h3>

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

                {/* Perfil de Riesgo Simple */}
                <div>
                  <span style={{ fontSize: '12px', color: '#94a3b8', fontWeight: 600 }}>
                    Nivel de Riesgo por Operación
                  </span>
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '6px', marginTop: '6px' }}>
                    <button
                      type="button"
                      className={`btn ${botRiskLevel === 'conservative' ? 'btn-primary' : 'btn-secondary'}`}
                      style={{ padding: '6px 8px', fontSize: '11px', justifyContent: 'center' }}
                      onClick={() => setBotRiskLevel('conservative')}
                    >
                      🛡️ 1% Seguro
                    </button>
                    <button
                      type="button"
                      className={`btn ${botRiskLevel === 'moderate' ? 'btn-primary' : 'btn-secondary'}`}
                      style={{ padding: '6px 8px', fontSize: '11px', justifyContent: 'center' }}
                      onClick={() => setBotRiskLevel('moderate')}
                    >
                      ⚖️ 2% Normal
                    </button>
                    <button
                      type="button"
                      className={`btn ${botRiskLevel === 'aggressive' ? 'btn-primary' : 'btn-secondary'}`}
                      style={{ padding: '6px 8px', fontSize: '11px', justifyContent: 'center' }}
                      onClick={() => setBotRiskLevel('aggressive')}
                    >
                      ⚡ 3% Ágil
                    </button>
                  </div>
                </div>

                {/* Auto-ejecución */}
                <label style={{ display: 'flex', alignItems: 'center', gap: '10px', cursor: 'pointer', fontSize: '13px' }}>
                  <input
                    type="checkbox"
                    checked={botAutoExecute}
                    onChange={(e) => setBotAutoExecute(e.target.checked)}
                    disabled={autonomousStatus?.is_running}
                  />
                  <span>Permitir al bot ejecutar compras/ventas automáticamente</span>
                </label>

                {/* Botón de Emergencia */}
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
                  🚨 Parada de Emergencia (Cerrar Todo)
                </button>
              </div>
            </div>
          </div>

          {/* Registro de Actividad y Pensamiento del Bot */}
          <div className="panel" style={{ padding: '20px' }}>
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
        </div>
      )}

      {/* ====================================================================
          PESTAÑA 2: MERCADO & GRÁFICO
          ==================================================================== */}
      {activeTab === 'market' && (
        <div className="panel" style={{ padding: '24px' }}>
          {/* Barra de herramientas del mercado */}
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              flexWrap: 'wrap',
              gap: '12px',
              marginBottom: '20px',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
              {/* Selector de Cripto */}
              <select
                className="select-input"
                style={{ padding: '8px 14px', fontSize: '14px', fontWeight: 700 }}
                value={selectedSymbol}
                onChange={(e) => setSelectedSymbol(e.target.value)}
              >
                {AVAILABLE_SYMBOLS.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.icon} {s.name}
                  </option>
                ))}
              </select>

              {/* Selector de Temporalidad */}
              <div style={{ display: 'flex', gap: '4px' }}>
                {TIMEFRAMES.map((tf) => (
                  <button
                    key={tf.id}
                    type="button"
                    className={`btn ${selectedTimeframe === tf.id ? 'btn-primary' : 'btn-secondary'}`}
                    style={{ padding: '6px 12px', fontSize: '12px' }}
                    onClick={() => setSelectedTimeframe(tf.id)}
                  >
                    {tf.id}
                  </button>
                ))}
              </div>
            </div>

            <button
              type="button"
              className="btn btn-secondary"
              style={{ fontSize: '13px', padding: '8px 14px' }}
              onClick={handleSyncMarket}
              disabled={isSyncing}
            >
              {isSyncing ? 'Sincronizando...' : '🔄 Actualizar Historial (14 días)'}
            </button>
          </div>

          {/* Gráfico Interactivo de Mercado */}
          <div
            style={{
              background: 'rgba(15, 23, 42, 0.6)',
              borderRadius: '16px',
              padding: '20px',
              border: '1px solid rgba(255, 255, 255, 0.08)',
              marginBottom: '24px',
            }}
          >
            {isLoadingCandles ? (
              <div style={{ textAlign: 'center', padding: '60px', color: '#94a3b8' }}>
                Cargando datos de mercado en vivo...
              </div>
            ) : (
              <MarketChart
                candles={candles}
                livePrice={livePrice}
                symbol={selectedSymbol}
                timeframe={selectedTimeframe}
              />
            )}
          </div>

          {/* Estadísticas Clave en Español */}
          {marketStats && (
            <div className="kpi-grid">
              <div className="kpi-card">
                <span className="kpi-label">Precio Actual</span>
                <span className="kpi-value" style={{ color: '#38bdf8' }}>
                  ${safeLoc(marketStats.currentPrice, { minimumFractionDigits: 2 })}
                </span>
                {lastTickTime && (
                  <span style={{ fontSize: '11px', color: '#94a3b8', marginTop: '4px' }}>
                    Último tick: {new Date(lastTickTime).toLocaleTimeString()}
                  </span>
                )}
              </div>

              <div className="kpi-card">
                <span className="kpi-label">Variación de la Vela</span>
                <span
                  className="kpi-value"
                  style={{ color: marketStats.change >= 0 ? '#10b981' : '#ef4444' }}
                >
                  {marketStats.change >= 0 ? '+' : ''}
                  {safeFix(marketStats.change)}%
                </span>
              </div>

              <div className="kpi-card">
                <span className="kpi-label">Rango Máximo del Día</span>
                <span className="kpi-value" style={{ color: '#10b981' }}>
                  ${safeFix(marketStats.high24h)}
                </span>
              </div>

              <div className="kpi-card">
                <span className="kpi-label">Rango Mínimo del Día</span>
                <span className="kpi-value" style={{ color: '#ef4444' }}>
                  ${safeFix(marketStats.low24h)}
                </span>
              </div>
            </div>
          )}
        </div>
      )}

      {/* ====================================================================
          PESTAÑA 3: CARTERA & OPERACIONES MANUALES
          ==================================================================== */}
      {activeTab === 'portfolio' && (
        <div>
          {/* Panel de Operación Rápida con 1 Clic */}
          <div className="panel" style={{ padding: '24px', marginBottom: '24px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
              <div>
                <h3 style={{ margin: 0, fontSize: '18px', fontWeight: 800 }}>
                  ⚡ Operación Rápida con 1 Clic
                </h3>
                <p style={{ margin: '4px 0 0', fontSize: '13px', color: '#94a3b8' }}>
                  Compra o vende fácilmente en modo {executionMode === 'PAPER' ? 'Simulado' : 'Real'}.
                </p>
              </div>

              {executionMode === 'PAPER' && (
                <button
                  type="button"
                  className="btn btn-secondary"
                  style={{ fontSize: '12px' }}
                  onClick={handleResetPaperAccount}
                >
                  🔄 Reiniciar Saldo ($10,000 USDT)
                </button>
              )}
            </div>

            {/* Selector de Monto */}
            <div style={{ maxWidth: '400px', marginBottom: '16px' }}>
              <span style={{ fontSize: '13px', color: '#94a3b8', fontWeight: 600 }}>
                Monto a Operar en Dólares (USD):
              </span>
              <div style={{ display: 'flex', gap: '8px', marginTop: '8px' }}>
                {[100, 250, 500, 1000].map((amt) => (
                  <button
                    key={amt}
                    type="button"
                    className={`btn ${orderAmountUsd === amt ? 'btn-primary' : 'btn-secondary'}`}
                    style={{ flex: 1, padding: '6px', fontSize: '13px' }}
                    onClick={() => setOrderAmountUsd(amt)}
                  >
                    ${amt}
                  </button>
                ))}
              </div>
            </div>

            {/* Botones Grandes Comprar / Vender */}
            <div className="quick-order-box">
              <button
                type="button"
                className="order-big-btn buy"
                onClick={() => handleQuickTrade('BUY')}
                disabled={isSubmittingOrder}
              >
                <span>🟢 COMPRAR {selectedSymbol.split('/')[0]}</span>
                <span style={{ fontSize: '12px', opacity: 0.9 }}>
                  Monto: ${orderAmountUsd} USD a precio de mercado
                </span>
              </button>

              <button
                type="button"
                className="order-big-btn sell"
                onClick={() => handleQuickTrade('SELL')}
                disabled={isSubmittingOrder}
              >
                <span>🔴 VENDER {selectedSymbol.split('/')[0]}</span>
                <span style={{ fontSize: '12px', opacity: 0.9 }}>
                  Monto: ${orderAmountUsd} USD a precio de mercado
                </span>
              </button>
            </div>
          </div>

          {/* Tabla de Posiciones Abiertas */}
          <div className="panel" style={{ padding: '24px' }}>
            <h3 style={{ margin: '0 0 16px', fontSize: '18px', fontWeight: 800 }}>
              📊 Posiciones Abiertas Actualmente
            </h3>

            {!portfolio?.positions || portfolio.positions.length === 0 ? (
              <div style={{ textAlign: 'center', padding: '30px', color: '#94a3b8', fontSize: '14px' }}>
                No tienes ninguna posición abierta en este momento. Utiliza los botones de compra arriba o enciende el bot autónomo.
              </div>
            ) : (
              <div className="table-container">
                <table className="candles-table">
                  <thead>
                    <tr>
                      <th style={{ textAlign: 'left' }}>Activo</th>
                      <th>Tipo</th>
                      <th>Cantidad</th>
                      <th>Precio Entrada</th>
                      <th>Precio Actual</th>
                      <th>Ganancia / Pérdida</th>
                      <th>Acción</th>
                    </tr>
                  </thead>
                  <tbody>
                    {portfolio.positions.map((pos) => {
                      const pnlPos = safeNum(pos.unrealized_pnl) >= 0;
                      return (
                        <tr key={pos.id}>
                          <td style={{ textAlign: 'left', fontWeight: 700 }}>{pos.symbol}</td>
                          <td>
                            <span
                              style={{
                                padding: '3px 8px',
                                borderRadius: '4px',
                                fontSize: '11px',
                                fontWeight: 800,
                                backgroundColor: pos.side === 'BUY' ? 'rgba(16, 185, 129, 0.2)' : 'rgba(239, 68, 68, 0.2)',
                                color: pos.side === 'BUY' ? '#10b981' : '#ef4444',
                              }}
                            >
                              {pos.side === 'BUY' ? 'COMPRA' : 'VENTA'}
                            </span>
                          </td>
                          <td>{pos.quantity}</td>
                          <td>${safeFix(pos.entry_price)}</td>
                          <td style={{ color: '#38bdf8', fontWeight: 600 }}>${safeFix(pos.current_price)}</td>
                          <td style={{ color: pnlPos ? '#10b981' : '#ef4444', fontWeight: 700 }}>
                            {pnlPos ? '+' : ''}${safeFix(pos.unrealized_pnl)} ({pnlPos ? '+' : ''}{safeFix(pos.unrealized_pnl_pct)}%)
                          </td>
                          <td>
                            <button
                              type="button"
                              className="btn btn-secondary"
                              style={{ padding: '4px 10px', fontSize: '11px' }}
                              onClick={() => handleClosePosition(pos.id)}
                            >
                              Cerrar Posición
                            </button>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      )}

      {/* ====================================================================
          PESTAÑA 4: COMITÉ DE ANALISTAS IA (VISUAL & FÁCIL)
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
          PESTAÑA 5: CONFIGURACIÓN & CLAVES (SIN RUIDO)
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

          {/* Trading Real y Alertas */}
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
                </div>
                <div style={{ marginTop: '12px', fontSize: '12px', color: '#94a3b8' }}>
                  Símbolos permitidos: <strong style={{ color: '#e2e8f0' }}>{realTradingStatus.allowed_symbols.join(', ') || 'sin restricción'}</strong>
                  {' · '}Confirmación manual: <strong style={{ color: '#e2e8f0' }}>{realTradingStatus.require_manual_confirmation ? 'Sí' : 'No'}</strong>
                  {' · '}Bot autónomo real: <strong style={{ color: realTradingStatus.autonomous_real_enabled ? '#f59e0b' : '#94a3b8' }}>{realTradingStatus.autonomous_real_enabled ? 'HABILITADO' : 'deshabilitado'}</strong>
                  {' · '}Protección: <strong style={{ color: '#f59e0b' }}>{realTradingStatus.protection_mode === 'local_guard' || !realTradingStatus.protection_mode ? 'Guardián local' : realTradingStatus.protection_mode}</strong>
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
                  <span className="kpi-label">Pérdida Diaria Máx. Permitida</span>
                  <span className="kpi-value">{safeFix(riskStatus.portfolio_daily_pnl_pct)}%</span>
                </div>
                <div className="kpi-card" style={{ padding: '12px' }}>
                  <span className="kpi-label">Drawdown Máximo de Cuenta</span>
                  <span className="kpi-value">{safeFix(riskStatus.portfolio_drawdown_pct)}%</span>
                </div>
                <div className="kpi-card" style={{ padding: '12px' }}>
                  <span className="kpi-label">Posiciones Simultáneas Máx.</span>
                  <span className="kpi-value">{riskStatus.max_allowed_positions} posiciones</span>
                </div>
              </div>
            </div>
          )}

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
      )}
    </div>
  );
}
