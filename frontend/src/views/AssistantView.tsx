import { useState } from 'react';
import toast from 'react-hot-toast';
import type { Page } from '../App';
import { Icon } from '../components/Icon';
import { MarketChart } from '../components/MarketChart';
import { Card, Disclosure, Empty, Notice, Pill, Segmented, Skeleton, Stat } from '../components/ui';
import { useMarketStream } from '../hooks/useMarketStream';
import { usePolling, type PollingState } from '../hooks/usePolling';
import { useI18n } from '../i18n/core';
import { logText, scanModeLabel } from '../i18n/labels';
import { apiGet, apiSend, errorMessage } from '../lib/api';
import { fmtDuration, fmtInt, fmtPrice, fmtSigned, fmtTime, readSignal } from '../lib/format';
import {
  AVAILABLE_SYMBOLS,
  TIMEFRAMES,
  type AutonomousBotConfig,
  type AutonomousBotStatus,
  type LLMScanModeData,
  type LLMScanModeValue,
  type MarketRegimeData,
  type RuntimeTradingConfigData,
  type TradingEnvironment,
} from '../types';

type RiskProfile = 'conservative' | 'moderate' | 'aggressive';

const RISK_PCT: Record<RiskProfile, number> = { conservative: 1, moderate: 2, aggressive: 3 };

interface Draft {
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
  const { t } = useI18n();
  const market = useMarketStream(symbol, timeframe);
  const [chartMode, setChartMode] = useState<'candles' | 'line'>('candles');

  const regime = usePolling<MarketRegimeData>(`regime|${symbol}|${timeframe}`, () =>
    apiGet<MarketRegimeData>(
      `/api/v1/market/${encodeURIComponent(symbol)}/regime?timeframe=${timeframe}&limit=200`,
    ),
  );

  const status = bot.data;
  const running = Boolean(status?.is_running);
  const signal = readSignal(status?.last_signal);
  const signalTitle = !signal.known
    ? t('signal.view.none.title')
    : signal.tone === 'wait'
      ? t('signal.view.wait.title')
      : t(`signal.view.${signal.tone}${signal.strong ? '.strong' : ''}.title`);
  const signalAdvice = t(signal.known ? `signal.view.${signal.tone}.advice` : 'signal.view.none.advice');
  const score = status?.last_confidence;
  const scorePct = typeof score === 'number' ? Math.round(score <= 1 ? score * 100 : score) : null;
  const firstCandle = market.candles[0];
  const sessionChange =
    firstCandle && market.livePrice ? ((market.livePrice - firstCandle.open) / firstCandle.open) * 100 : null;

  return (
    <div className="assistant-grid">
      <div className="stack-lg">
        <section className="card card-flush" aria-label={t('assistant.market')}>
          <div className="chart-toolbar">
            <div className="price-head">
              <label className="sr-only" htmlFor="symbol">
                {t('assistant.market')}
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
                  {t('assistant.chartChange', { value: fmtSigned(sessionChange, 2, ' %') })}
                </span>
              )}
              <Pill dot live={market.connected} tone={market.connected ? 'default' : 'warn'}>
                {market.connected ? t('assistant.live') : t('assistant.connecting')}
              </Pill>
            </div>
            <div className="row">
              <Segmented
                label={t('assistant.timeframe')}
                value={timeframe}
                onChange={onTimeframeChange}
                options={TIMEFRAMES.map((item) => ({ value: item.id, label: t(`timeframe.${item.id}`) }))}
              />
              <Segmented
                label={t('assistant.chartType')}
                value={chartMode}
                onChange={setChartMode}
                options={[
                  { value: 'candles', label: t('assistant.candles') },
                  { value: 'line', label: t('assistant.line') },
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

        <Card title={t('assistant.reading.title')} subtitle={t('assistant.reading.subtitle')}>
          <div className="stack">
            <div className="verdict">
              <div className={`verdict-icon ${signal.tone}`}>
                <Icon name={signal.tone === 'buy' ? 'arrowUp' : signal.tone === 'sell' ? 'arrowDown' : 'minus'} />
              </div>
              <div>
                <div className="verdict-title">{signalTitle}</div>
                <p className="muted small" style={{ marginTop: 4 }}>
                  {signalAdvice}
                </p>
              </div>
            </div>

            <div className="row">
              {scorePct !== null && (
                <Pill title={t('assistant.strengthHint')}>{t('assistant.strength', { value: scorePct })}</Pill>
              )}
              {regime.data?.status === 'classified' && regime.data.trend && (
                <Pill>{t(`regime.trend.${regime.data.trend}`)}</Pill>
              )}
              {regime.data?.status === 'classified' && regime.data.volatility && (
                <Pill tone={regime.data.volatility === 'high' ? 'warn' : 'default'}>
                  {t(`regime.volatility.${regime.data.volatility}`)}
                </Pill>
              )}
              {regime.data?.metrics.data_stale && <Pill tone="warn">{t('assistant.staleData')}</Pill>}
              {status?.last_symbol && <Pill>{t('assistant.lastAnalyzed', { symbol: status.last_symbol })}</Pill>}
            </div>

            {status?.last_error && <Notice tone="warn">{t('assistant.lastError', { detail: status.last_error })}</Notice>}

            {status?.config?.llm_analysis_interval_seconds ? (
              <p className="muted xsmall">
                {t('assistant.cadence', {
                  analysis: fmtDuration(status.config.llm_analysis_interval_seconds),
                  last: status.last_analysis_at
                    ? t('assistant.cadenceLast', { time: fmtTime(status.last_analysis_at) })
                    : '',
                  exits: fmtDuration(status.config.interval_seconds),
                })}
              </p>
            ) : null}
          </div>
        </Card>

        <ActivityLog status={status} />
      </div>

      <div className="stack-lg sticky">
        <BotControl symbol={symbol} timeframe={timeframe} bot={bot} runtime={runtime} navigate={navigate} />
        {!running && (status?.logs?.length ?? 0) === 0 && <GettingStarted runtime={runtime.data} navigate={navigate} />}
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
  const { t } = useI18n();
  const status = bot.data;
  const running = Boolean(status?.is_running);
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState<Draft>({
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
          interval: config.interval_seconds,
          minScore: config.min_confidence_pct,
          maxPositions: config.max_open_positions,
          risk:
            config.risk_per_trade_pct >= 3
              ? 'aggressive'
              : config.risk_per_trade_pct >= 2
                ? 'moderate'
                : 'conservative',
          autoExecute: config.auto_execute,
        }
      : draft;
  const update = (patch: Partial<Draft>) => setDraft((current) => ({ ...current, ...patch }));
  const environment = runtime.data?.trading_environment ?? 'testnet';
  const mainnet = environment === 'mainnet';
  const [switching, setSwitching] = useState(false);

  // The main switch: Testnet (test funds) or Mainnet (real money). It changes
  // the server's active network; the assistant always trades on that network.
  const switchNetwork = async (target: TradingEnvironment) => {
    if (target === environment || switching || running) return;
    if (target === 'mainnet') {
      if (!runtime.data?.credentials.mainnet) {
        toast.error(t('bot.toast.needKeysMainnet'));
        navigate('settings', 'cuenta');
        return;
      }
      if (!window.confirm(t('bot.confirmMainnet', { max: String(runtime.data.real_trading_max_notional) }))) return;
    }
    setSwitching(true);
    try {
      const body =
        target === 'mainnet'
          ? { trading_environment: 'mainnet', allow_mainnet_trading: true, confirm: true }
          : { trading_environment: 'testnet' };
      runtime.setData(await apiSend<RuntimeTradingConfigData>('PUT', '/api/v1/trading/runtime-config', body));
      toast.success(t(target === 'mainnet' ? 'bot.toast.onMainnet' : 'bot.toast.onTestnet'));
    } catch (error) {
      toast.error(t('common.saveFailed', { detail: errorMessage(error) }));
    } finally {
      setSwitching(false);
    }
  };

  const changeScanMode = async (mode: LLMScanModeValue) => {
    if (savingScanMode || scanMode.data?.mode === mode) return;
    setSavingScanMode(true);
    try {
      const data = await apiSend<LLMScanModeData>('PUT', '/api/v1/agents/llm/scan-mode', { mode });
      scanMode.setData(data);
      toast.success(t('bot.toast.scanMode', { mode: scanModeLabel(t, data.mode, data.active.label) }));
    } catch (error) {
      toast.error(t('common.saveFailed', { detail: errorMessage(error) }));
    } finally {
      setSavingScanMode(false);
    }
  };

  const start = async () => {
    if (!runtime.data?.credentials[environment]) {
      toast.error(t(mainnet ? 'bot.toast.needKeysMainnet' : 'bot.toast.needKeysTestnet'));
      navigate('settings', 'cuenta');
      return;
    }
    if (mainnet && !runtime.data.allow_mainnet_trading) {
      toast.error(t('bot.toast.needMainnetPermission'));
      navigate('settings', 'proteccion');
      return;
    }
    // Real money gets a final confirmation; Testnet uses test funds only.
    if (mainnet) {
      const confirmed = window.confirm(
        t('bot.confirmReal', {
          venue: t(mainnet ? 'bot.venue.mainnet' : 'bot.venue.testnet'),
          symbol,
          risk: RISK_PCT[draft.risk],
          max: String(runtime.data.real_trading_max_notional),
          positions: draft.maxPositions,
        }),
      );
      if (!confirmed) return;
    }

    setBusy(true);
    try {
      const ready =
        runtime.data.execution_mode === 'real' &&
        runtime.data.enable_real_trading &&
        runtime.data.enable_autonomous_real_trading;
      if (!ready) {
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
        execution_mode: 'real',
        confirm_real_trading: true,
        min_confidence_pct: draft.minScore,
        risk_per_trade_pct: RISK_PCT[draft.risk],
        max_open_positions: draft.maxPositions,
        auto_execute: draft.autoExecute,
        stop_loss_pct: 2,
        take_profit_pct: 4,
      };
      bot.setData(await apiSend<AutonomousBotStatus>('POST', '/api/v1/autonomous/start', payload));
      toast.success(t('bot.toast.started', { symbol }));
    } catch (error) {
      toast.error(t('bot.toast.startFailed', { detail: errorMessage(error) }));
    } finally {
      setBusy(false);
    }
  };

  const stop = async () => {
    setBusy(true);
    try {
      bot.setData(await apiSend<AutonomousBotStatus>('POST', '/api/v1/autonomous/stop'));
      toast.success(t('bot.toast.stopped'));
    } catch (error) {
      toast.error(t('bot.toast.stopFailed', { detail: errorMessage(error) }));
    } finally {
      setBusy(false);
    }
  };

  const emergency = async () => {
    if (!window.confirm(t('bot.confirmEmergency'))) return;
    try {
      bot.setData(await apiSend<AutonomousBotStatus>('POST', '/api/v1/autonomous/emergency-kill'));
      toast.success(t('bot.toast.emergency'));
    } catch (error) {
      toast.error(t('bot.toast.emergencyFailed', { detail: errorMessage(error) }));
    }
  };

  const venue = t(mainnet ? 'venue.mainnet' : 'venue.testnet');

  return (
    <Card>
      <div className="stack">
        <div className="bot-state">
          <div className={`bot-state-icon ${running ? 'running' : ''}`}>
            <Icon name={running ? 'activity' : 'pause'} />
          </div>
          <div>
            <div className="strong">{running ? t('bot.running') : t('bot.paused')}</div>
            <div className="muted xsmall">
              {running
                ? t('bot.runningFor', { venue, uptime: fmtDuration(status?.uptime_seconds) })
                : t('bot.configureHint')}
            </div>
          </div>
        </div>

        <div className="stats" style={{ gridTemplateColumns: 'repeat(3, 1fr)' }}>
          <Stat label={t('bot.positions')} value={fmtInt(status?.active_positions_count ?? 0)} />
          <Stat label={t('bot.trades')} value={fmtInt(status?.trades_executed ?? 0)} />
          <Stat label={t('bot.cycles')} value={fmtInt(status?.total_cycles ?? 0)} />
        </div>

        <div className="field">
          <span className="field-label">{t('bot.where')}</span>
          <Segmented
            label={t('bot.where')}
            block
            disabled={running || switching || !runtime.data}
            value={environment}
            onChange={(target) => void switchNetwork(target)}
            options={[
              { value: 'testnet', label: t('venue.testnet') },
              { value: 'mainnet', label: t('venue.mainnet') },
            ]}
          />
          <span className="field-hint">
            {running ? t('bot.where.locked') : t(mainnet ? 'bot.where.mainnet' : 'bot.where.testnet')}
          </span>
          {mainnet && <Notice tone="error">{t('bot.where.mainnetWarning')}</Notice>}
        </div>

        <div className="field">
          <span className="field-label">{t('bot.risk')}</span>
          <Segmented
            label={t('bot.risk')}
            block
            disabled={running}
            value={shown.risk}
            onChange={(risk) => update({ risk })}
            options={[
              { value: 'conservative', label: t('bot.risk.low') },
              { value: 'moderate', label: t('bot.risk.medium') },
              { value: 'aggressive', label: t('bot.risk.high') },
            ]}
          />
          <span className="field-hint">{t('bot.risk.hint')}</span>
        </div>

        <div className="field">
          <span className="field-label">{t('bot.ai')}</span>
          <Segmented
            label={t('bot.ai')}
            block
            disabled={savingScanMode || !scanMode.data}
            value={scanMode.data?.mode ?? 'savings'}
            onChange={(mode) => void changeScanMode(mode)}
            options={(scanMode.data?.presets ?? []).map((preset) => ({
              value: preset.mode,
              label: scanModeLabel(t, preset.mode, preset.label),
              title: preset.description,
            }))}
          />
          {scanMode.data && (
            <span className="field-hint">
              {t('bot.ai.hint', {
                markets: scanMode.data.active.max_markets,
                how: t(scanMode.data.active.parallel ? 'common.parallel' : 'common.sequential'),
              })}
            </span>
          )}
        </div>

        <Disclosure summary={t('bot.strategy')}>
          <label className="field">
            <span className="field-label">{t('bot.interval')}</span>
            <select
              className="select"
              disabled={running}
              value={shown.interval}
              onChange={(event) => update({ interval: Number(event.target.value) })}
            >
              <option value={30}>{t('bot.interval.30')}</option>
              <option value={60}>{t('bot.interval.60')}</option>
              <option value={120}>{t('bot.interval.120')}</option>
              <option value={300}>{t('bot.interval.300')}</option>
            </select>
          </label>
          <label className="field">
            <span className="field-label">{t('bot.minScore', { value: shown.minScore })}</span>
            <input
              type="range"
              min={50}
              max={90}
              step={5}
              disabled={running}
              value={shown.minScore}
              onChange={(event) => update({ minScore: Number(event.target.value) })}
            />
            <span className="field-hint">{t('bot.minScore.hint')}</span>
          </label>
          <label className="field">
            <span className="field-label">{t('bot.maxPositions')}</span>
            <select
              className="select"
              disabled={running}
              value={shown.maxPositions}
              onChange={(event) => update({ maxPositions: Number(event.target.value) })}
            >
              <option value={1}>{t('bot.maxPositions.1')}</option>
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
              {t('bot.autoExecute')}
              <span className="check-hint">{t('bot.autoExecute.hint')}</span>
            </span>
          </label>
          <p className="muted xsmall">{t('bot.stopsNote')}</p>
        </Disclosure>

        <button
          type="button"
          className={`btn btn-lg btn-block ${running ? '' : 'btn-primary'}`}
          disabled={busy || !status}
          onClick={() => void (running ? stop() : start())}
        >
          <Icon name={running ? 'pause' : 'play'} />
          {busy ? t('common.processing') : running ? t('bot.pause') : t('bot.start')}
        </button>

        <Disclosure summary={t('bot.emergency')}>
          <p className="muted xsmall">{t('bot.emergency.hint')}</p>
          <button type="button" className="btn btn-danger btn-block" onClick={() => void emergency()}>
            <Icon name="stop" size={16} />
            {t('bot.emergency.button')}
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
  const { t } = useI18n();
  const hasTestnetKeys = Boolean(runtime?.credentials?.testnet);
  return (
    <Card title={t('start.title')}>
      <ol className="steps">
        <li className="done">
          <span>
            {t('start.market')}
            <span className="muted">{t('start.market.hint')}</span>
          </span>
        </li>
        <li className={hasTestnetKeys ? 'done' : ''}>
          <span>
            {t('start.keys')}
            <span className="muted">
              {hasTestnetKeys ? t('start.keys.done') : t('start.keys.hint')}{' '}
              {!hasTestnetKeys && (
                <button type="button" className="btn btn-sm btn-ghost" onClick={() => navigate('settings', 'cuenta')}>
                  {t('start.keys.connect')}
                </button>
              )}
            </span>
          </span>
        </li>
        <li>
          <span>
            {t('start.testnet')}
            <span className="muted">{t('start.testnet.hint')}</span>
          </span>
        </li>
        <li>
          <span>
            {t('start.go')}
            <span className="muted">{t('start.go.hint')}</span>
          </span>
        </li>
      </ol>
    </Card>
  );
}

function ActivityLog({ status }: { status: AutonomousBotStatus | null }) {
  const { t } = useI18n();
  const logs = status?.logs ?? [];
  return (
    <Card title={t('activity.title')} subtitle={t('activity.subtitle')}>
      {logs.length === 0 ? (
        <Empty>{t('activity.empty')}</Empty>
      ) : (
        <div className="log" aria-live="polite">
          {logs.slice(0, 40).map((log) => (
            <div key={log.id} className={`log-row ${log.level.toLowerCase()}`}>
              <span className="log-time">{fmtTime(log.timestamp)}</span>
              <span className="log-msg">
                {logText(t, log)}
                {log.price && !log.key ? <span className="muted num"> · {fmtPrice(log.price)}</span> : null}
              </span>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}
