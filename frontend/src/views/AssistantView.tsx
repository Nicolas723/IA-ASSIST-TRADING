import { useState } from 'react';
import toast from 'react-hot-toast';
import type { Page } from '../App';
import { Icon } from '../components/Icon';
import { MarketChart } from '../components/MarketChart';
import { Card, Disclosure, Empty, Notice, Pill, Segmented, Skeleton, Stat } from '../components/ui';
import { useMarketStream } from '../hooks/useMarketStream';
import { usePolling, type PollingState } from '../hooks/usePolling';
import { apiGet, apiSend, errorMessage } from '../lib/api';
import { describeSignal, fmtDuration, fmtInt, fmtPrice, fmtTime } from '../lib/format';
import {
  AVAILABLE_SYMBOLS,
  TIMEFRAMES,
  type AutonomousBotConfig,
  type AutonomousBotStatus,
  type LLMScanModeData,
  type LLMScanModeValue,
  type MarketRegimeData,
  type RuntimeTradingConfigData,
} from '../types';

type RiskProfile = 'conservative' | 'moderate' | 'aggressive';

const RISK_PCT: Record<RiskProfile, number> = { conservative: 1, moderate: 2, aggressive: 3 };

const TREND_LABEL: Record<string, string> = {
  uptrend: 'Tendencia alcista',
  downtrend: 'Tendencia bajista',
  range: 'Lateral',
};

const VOLATILITY_LABEL: Record<string, string> = {
  low: 'Volatilidad baja',
  normal: 'Volatilidad normal',
  high: 'Volatilidad alta',
};

interface Draft {
  mode: 'paper' | 'real';
  interval: number;
  minScore: number;
  maxPositions: number;
  risk: RiskProfile;
  autoExecute: boolean;
}

interface AssistantViewProps {
  symbol: string;
  timeframe: string;
  onSymbolChange: (symbol: string) => void;
  onTimeframeChange: (timeframe: string) => void;
  bot: PollingState<AutonomousBotStatus>;
  runtime: PollingState<RuntimeTradingConfigData>;
  navigate: (page: Page, section?: string) => void;
}

export function AssistantView({
  symbol,
  timeframe,
  onSymbolChange,
  onTimeframeChange,
  bot,
  runtime,
  navigate,
}: AssistantViewProps) {
  const market = useMarketStream(symbol, timeframe);
  const [chartMode, setChartMode] = useState<'candles' | 'line'>('candles');

  const regime = usePolling<MarketRegimeData>(`regime|${symbol}|${timeframe}`, () =>
    apiGet<MarketRegimeData>(
      `/api/v1/market/${encodeURIComponent(symbol)}/regime?timeframe=${timeframe}&limit=200`,
    ),
  );

  const status = bot.data;
  const running = Boolean(status?.is_running);
  const signal = describeSignal(status?.last_signal);
  const score = status?.last_confidence;
  const scorePct = typeof score === 'number' ? Math.round((score <= 1 ? score * 100 : score)) : null;
  const firstCandle = market.candles[0];
  const sessionChange =
    firstCandle && market.livePrice ? ((market.livePrice - firstCandle.open) / firstCandle.open) * 100 : null;

  return (
    <div className="assistant-grid">
      <div className="stack-lg">
        <section className="card card-flush" aria-label="Mercado">
          <div className="chart-toolbar">
            <div className="price-head">
              <label className="sr-only" htmlFor="symbol">
                Mercado
              </label>
              <select
                id="symbol"
                className="select"
                style={{ width: 'auto', minWidth: 150 }}
                value={symbol}
                onChange={(event) => onSymbolChange(event.target.value)}
              >
                {AVAILABLE_SYMBOLS.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name} · {item.ticker}
                  </option>
                ))}
              </select>
              <span className="price-big">{fmtPrice(market.livePrice)}</span>
              {sessionChange !== null && (
                <span className={`small num ${sessionChange >= 0 ? 'pos' : 'neg'}`}>
                  {sessionChange >= 0 ? '+' : ''}
                  {sessionChange.toFixed(2)} % en el gráfico
                </span>
              )}
              <Pill dot live={market.connected} tone={market.connected ? 'default' : 'warn'}>
                {market.connected ? 'En vivo' : 'Conectando'}
              </Pill>
            </div>
            <div className="row">
              <Segmented
                label="Temporalidad"
                value={timeframe}
                onChange={onTimeframeChange}
                options={TIMEFRAMES.map((item) => ({ value: item.id, label: item.label }))}
              />
              <Segmented
                label="Tipo de gráfico"
                value={chartMode}
                onChange={setChartMode}
                options={[
                  { value: 'candles', label: 'Velas' },
                  { value: 'line', label: 'Línea' },
                ]}
              />
            </div>
          </div>
          <div className="chart-body">
            {market.loading && market.candles.length === 0 ? (
              <Skeleton height={300} />
            ) : (
              <MarketChart candles={market.candles} livePrice={market.livePrice} mode={chartMode} />
            )}
          </div>
        </section>

        <Card title="Qué ve el asistente" subtitle="Lectura del comité de analistas en lenguaje sencillo.">
          <div className="stack">
            <div className="verdict">
              <div className={`verdict-icon ${signal.tone}`}>
                <Icon name={signal.tone === 'buy' ? 'arrowUp' : signal.tone === 'sell' ? 'arrowDown' : 'minus'} />
              </div>
              <div>
                <div className="verdict-title">{signal.title}</div>
                <p className="muted small" style={{ marginTop: 4 }}>
                  {signal.advice}
                </p>
              </div>
            </div>

            <div className="row">
              {scorePct !== null && (
                <Pill title="Fuerza de la señal del comité. No es una probabilidad de éxito.">
                  Fuerza {scorePct} %
                </Pill>
              )}
              {regime.data?.status === 'classified' && regime.data.trend && (
                <Pill>{TREND_LABEL[regime.data.trend]}</Pill>
              )}
              {regime.data?.status === 'classified' && regime.data.volatility && (
                <Pill tone={regime.data.volatility === 'high' ? 'warn' : 'default'}>
                  {VOLATILITY_LABEL[regime.data.volatility]}
                </Pill>
              )}
              {regime.data?.metrics.data_stale && <Pill tone="warn">Datos antiguos</Pill>}
              {status?.last_symbol && <Pill>Último analizado: {status.last_symbol}</Pill>}
            </div>

            {status?.last_error && <Notice tone="warn">Último aviso del asistente: {status.last_error}</Notice>}

            {status?.config?.llm_analysis_interval_seconds ? (
              <p className="muted xsmall">
                El comité analiza cada {fmtDuration(status.config.llm_analysis_interval_seconds)}
                {status.last_analysis_at ? ` (último a las ${fmtTime(status.last_analysis_at)})` : ''}. Las salidas y
                los stops se vigilan cada {fmtDuration(status.config.interval_seconds)}.
              </p>
            ) : null}
          </div>
        </Card>

        <ActivityLog status={status} />
      </div>

      <div className="stack-lg sticky">
        <BotControl
          symbol={symbol}
          timeframe={timeframe}
          bot={bot}
          runtime={runtime}
          navigate={navigate}
        />
        {!running && (status?.logs?.length ?? 0) === 0 && (
          <GettingStarted runtime={runtime.data} navigate={navigate} />
        )}
      </div>
    </div>
  );
}

function BotControl({
  symbol,
  timeframe,
  bot,
  runtime,
  navigate,
}: {
  symbol: string;
  timeframe: string;
  bot: PollingState<AutonomousBotStatus>;
  runtime: PollingState<RuntimeTradingConfigData>;
  navigate: (page: Page, section?: string) => void;
}) {
  const status = bot.data;
  const running = Boolean(status?.is_running);
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState<Draft>({
    mode: 'paper',
    interval: 60,
    minScore: 60,
    maxPositions: 1,
    risk: 'conservative',
    autoExecute: true,
  });
  const scanMode = usePolling<LLMScanModeData>('scan-mode', () =>
    apiGet<LLMScanModeData>('/api/v1/agents/llm/scan-mode'),
  );
  const [savingScanMode, setSavingScanMode] = useState(false);

  // While running, controls show the live session; while stopped, the draft.
  const config = status?.config;
  const shown: Draft =
    running && config
      ? {
          mode: config.execution_mode === 'real' ? 'real' : 'paper',
          interval: config.interval_seconds,
          minScore: config.min_confidence_pct,
          maxPositions: config.max_open_positions,
          risk: config.risk_per_trade_pct >= 3 ? 'aggressive' : config.risk_per_trade_pct >= 2 ? 'moderate' : 'conservative',
          autoExecute: config.auto_execute,
        }
      : draft;
  const update = (patch: Partial<Draft>) => setDraft((current) => ({ ...current, ...patch }));
  const environment = runtime.data?.trading_environment ?? 'testnet';

  const changeScanMode = async (mode: LLMScanModeValue) => {
    if (savingScanMode || scanMode.data?.mode === mode) return;
    setSavingScanMode(true);
    try {
      const data = await apiSend<LLMScanModeData>('PUT', '/api/v1/agents/llm/scan-mode', { mode });
      scanMode.setData(data);
      toast.success(`Consumo de IA: ${data.active.label}. Se aplica en el próximo análisis.`);
    } catch (error) {
      toast.error(`No se pudo cambiar: ${errorMessage(error)}`);
    } finally {
      setSavingScanMode(false);
    }
  };

  const start = async () => {
    if (draft.mode === 'real') {
      if (!runtime.data?.credentials[environment]) {
        toast.error(`Primero conecta tus claves de ${environment === 'mainnet' ? 'Mainnet' : 'Testnet'}.`);
        navigate('settings', 'cuenta');
        return;
      }
      if (environment === 'mainnet' && !runtime.data.allow_mainnet_trading) {
        toast.error('Mainnet requiere activar el permiso de dinero real en Configuración.');
        navigate('settings', 'proteccion');
        return;
      }
      const venue = environment === 'mainnet' ? 'Binance con DINERO REAL' : 'Binance Testnet (fondos de prueba)';
      const confirmed = window.confirm(
        `El asistente podrá enviar órdenes en ${venue} durante esta sesión.\n\n` +
          `Mercado: ${symbol} · Riesgo por operación: ${RISK_PCT[draft.risk]} % · Máximo por orden: ` +
          `${runtime.data.real_trading_max_notional} USDT · Posiciones a la vez: ${draft.maxPositions}.\n\n` +
          'Los límites de riesgo y las protecciones siguen activos. ¿Iniciar?',
      );
      if (!confirmed) return;
    }

    setBusy(true);
    try {
      if (draft.mode === 'real') {
        const updated = await apiSend<RuntimeTradingConfigData>('PUT', '/api/v1/trading/runtime-config', {
          execution_mode: 'real',
          enable_real_trading: true,
          enable_autonomous_real_trading: true,
          require_manual_confirmation: true,
          confirm: true,
        });
        runtime.setData(updated);
      }
      const payload: AutonomousBotConfig = {
        symbol,
        timeframe,
        interval_seconds: draft.interval,
        execution_mode: draft.mode,
        confirm_real_trading: draft.mode === 'real',
        min_confidence_pct: draft.minScore,
        risk_per_trade_pct: RISK_PCT[draft.risk],
        max_open_positions: draft.maxPositions,
        auto_execute: draft.autoExecute,
        stop_loss_pct: 2,
        take_profit_pct: 4,
      };
      bot.setData(await apiSend<AutonomousBotStatus>('POST', '/api/v1/autonomous/start', payload));
      toast.success(`Asistente activo en ${symbol}.`);
    } catch (error) {
      toast.error(`No se pudo iniciar: ${errorMessage(error)}`);
    } finally {
      setBusy(false);
    }
  };

  const stop = async () => {
    setBusy(true);
    try {
      bot.setData(await apiSend<AutonomousBotStatus>('POST', '/api/v1/autonomous/stop'));
      toast.success('Asistente en pausa. Las posiciones abiertas se conservan.');
    } catch (error) {
      toast.error(`No se pudo pausar: ${errorMessage(error)}`);
    } finally {
      setBusy(false);
    }
  };

  const emergency = async () => {
    if (!window.confirm('Parada de emergencia: se detiene el asistente y se cierran sus posiciones ahora. ¿Continuar?')) {
      return;
    }
    try {
      bot.setData(await apiSend<AutonomousBotStatus>('POST', '/api/v1/autonomous/emergency-kill'));
      toast.success('Parada de emergencia ejecutada.');
    } catch (error) {
      toast.error(`Error en la parada: ${errorMessage(error)}`);
    }
  };

  return (
    <Card>
      <div className="stack">
        <div className="bot-state">
          <div className={`bot-state-icon ${running ? 'running' : ''}`}>
            <Icon name={running ? 'activity' : 'pause'} />
          </div>
          <div>
            <div className="strong">{running ? 'Asistente activo' : 'Asistente en pausa'}</div>
            <div className="muted xsmall">
              {running
                ? `${shown.mode === 'real' ? (environment === 'mainnet' ? 'Dinero real' : 'Testnet') : 'Simulado'} · ${fmtDuration(status?.uptime_seconds)} en marcha`
                : 'Configura y pulsa iniciar.'}
            </div>
          </div>
        </div>

        <div className="stats" style={{ gridTemplateColumns: 'repeat(3, 1fr)' }}>
          <Stat label="Posiciones" value={fmtInt(status?.active_positions_count ?? 0)} />
          <Stat label="Operaciones" value={fmtInt(status?.trades_executed ?? 0)} />
          <Stat label="Ciclos" value={fmtInt(status?.total_cycles ?? 0)} />
        </div>

        <div className="field">
          <span className="field-label">Dónde opera</span>
          <Segmented
            label="Modo de ejecución"
            block
            disabled={running}
            value={shown.mode}
            onChange={(mode) => update({ mode })}
            options={[
              { value: 'paper', label: 'Simulado' },
              { value: 'real', label: environment === 'mainnet' ? 'Dinero real' : 'Testnet' },
            ]}
          />
          <span className="field-hint">
            {shown.mode === 'paper'
              ? 'Practica con saldo virtual. No se envía ninguna orden al exchange.'
              : environment === 'mainnet'
                ? 'Usará tu saldo real en Binance. Revisa los límites antes de iniciar.'
                : 'Órdenes reales en la red de pruebas de Binance, sin dinero real.'}
          </span>
        </div>

        <div className="field">
          <span className="field-label">Nivel de riesgo</span>
          <Segmented
            label="Nivel de riesgo"
            block
            disabled={running}
            value={shown.risk}
            onChange={(risk) => update({ risk })}
            options={[
              { value: 'conservative', label: 'Bajo · 1 %' },
              { value: 'moderate', label: 'Medio · 2 %' },
              { value: 'aggressive', label: 'Alto · 3 %' },
            ]}
          />
          <span className="field-hint">Lo máximo que se arriesga del saldo en cada operación hasta su stop.</span>
        </div>

        <div className="field">
          <span className="field-label">Consumo de IA</span>
          <Segmented
            label="Consumo de IA"
            block
            disabled={savingScanMode || !scanMode.data}
            value={scanMode.data?.mode ?? 'savings'}
            onChange={(mode) => void changeScanMode(mode)}
            options={(scanMode.data?.presets ?? []).map((preset) => ({
              value: preset.mode,
              label: preset.label,
              title: preset.description,
            }))}
          />
          {scanMode.data && (
            <span className="field-hint">
              {scanMode.data.active.max_markets} mercado(s) por análisis,{' '}
              {scanMode.data.active.parallel ? 'en paralelo' : 'uno tras otro'}. Se puede cambiar en marcha.
            </span>
          )}
        </div>

        <Disclosure summary="Ajustes de estrategia">
          <label className="field">
            <span className="field-label">Revisar el mercado cada</span>
            <select
              className="select"
              disabled={running}
              value={shown.interval}
              onChange={(event) => update({ interval: Number(event.target.value) })}
            >
              <option value={30}>30 segundos</option>
              <option value={60}>1 minuto (recomendado)</option>
              <option value={120}>2 minutos</option>
              <option value={300}>5 minutos</option>
            </select>
          </label>
          <label className="field">
            <span className="field-label">Fuerza mínima de la señal: {shown.minScore} %</span>
            <input
              type="range"
              min={50}
              max={90}
              step={5}
              disabled={running}
              value={shown.minScore}
              onChange={(event) => update({ minScore: Number(event.target.value) })}
            />
            <span className="field-hint">Más alto, menos operaciones. No es una probabilidad de acierto.</span>
          </label>
          <label className="field">
            <span className="field-label">Posiciones abiertas a la vez</span>
            <select
              className="select"
              disabled={running}
              value={shown.maxPositions}
              onChange={(event) => update({ maxPositions: Number(event.target.value) })}
            >
              <option value={1}>1 (recomendado)</option>
              <option value={2}>2</option>
              <option value={3}>3</option>
            </select>
          </label>
          <label className="check">
            <input
              type="checkbox"
              disabled={running}
              checked={shown.autoExecute}
              onChange={(event) => update({ autoExecute: event.target.checked })}
            />
            <span>
              Ejecutar las señales automáticamente
              <span className="check-hint">Desactívalo para que solo analice y registre.</span>
            </span>
          </label>
          <p className="muted xsmall">Cada operación usa stop al 2 % y objetivo al 4 % (relación 2:1).</p>
        </Disclosure>

        <button
          type="button"
          className={`btn btn-lg btn-block ${running ? '' : 'btn-primary'}`}
          disabled={busy || !status}
          onClick={() => void (running ? stop() : start())}
        >
          <Icon name={running ? 'pause' : 'play'} />
          {busy ? 'Procesando…' : running ? 'Pausar asistente' : 'Iniciar asistente'}
        </button>

        <Disclosure summary="Emergencia">
          <p className="muted xsmall">Detiene el asistente y cierra al momento todas las posiciones que abrió.</p>
          <button type="button" className="btn btn-danger btn-block" onClick={() => void emergency()}>
            <Icon name="stop" size={16} />
            Detener y cerrar posiciones
          </button>
        </Disclosure>
      </div>
    </Card>
  );
}

function GettingStarted({
  runtime,
  navigate,
}: {
  runtime: RuntimeTradingConfigData | null;
  navigate: (page: Page, section?: string) => void;
}) {
  const hasKeys = Boolean(runtime?.credentials?.testnet || runtime?.credentials?.mainnet);
  return (
    <Card title="Primeros pasos">
      <ol className="steps">
        <li className="done">
          <span>
            Elige un mercado
            <span className="muted">Bitcoin está seleccionado por defecto.</span>
          </span>
        </li>
        <li>
          <span>
            Empieza en modo simulado
            <span className="muted">Observa cómo decide sin arriesgar dinero.</span>
          </span>
        </li>
        <li className={hasKeys ? 'done' : ''}>
          <span>
            Conecta Binance (opcional)
            <span className="muted">
              {hasKeys ? 'Claves guardadas y cifradas.' : 'Necesario solo para Testnet o dinero real. '}
              {!hasKeys && (
                <button type="button" className="btn btn-sm btn-ghost" onClick={() => navigate('settings', 'cuenta')}>
                  Conectar
                </button>
              )}
            </span>
          </span>
        </li>
        <li>
          <span>
            Pulsa «Iniciar asistente»
            <span className="muted">Puedes pausarlo en cualquier momento.</span>
          </span>
        </li>
      </ol>
    </Card>
  );
}

function ActivityLog({ status }: { status: AutonomousBotStatus | null }) {
  const logs = status?.logs ?? [];
  return (
    <Card title="Actividad" subtitle="Lo que el asistente ha hecho y por qué, del más reciente al más antiguo.">
      {logs.length === 0 ? (
        <Empty>Sin actividad todavía. Aparecerá aquí en cuanto inicies el asistente.</Empty>
      ) : (
        <div className="log" aria-live="polite">
          {logs.slice(0, 40).map((log) => (
            <div key={log.id} className={`log-row ${log.level.toLowerCase()}`}>
              <span className="log-time">{fmtTime(log.timestamp)}</span>
              <span className="log-msg">
                {log.message}
                {log.price ? <span className="muted num"> · {fmtPrice(log.price)}</span> : null}
              </span>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}
