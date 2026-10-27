import { useState } from 'react';
import toast from 'react-hot-toast';
import type { Page } from '../App';
import AccountBalanceCard from '../components/AccountBalanceCard';
import { AlertSettings, LlmSettings } from '../components/IntegrationsSettings';
import { Card, Notice, PageHead, Pill, Segmented, Skeleton, Stat, SubNav } from '../components/ui';
import { useIntegrations } from '../hooks/useIntegrations';
import { usePolling, type PollingState } from '../hooks/usePolling';
import { useI18n, type MessageKey } from '../i18n/core';
import { apiGet, apiSend, errorMessage } from '../lib/api';
import { fmtUsd } from '../lib/format';
import type {
  AlertStatusData,
  ConnectionPingResult,
  EncryptedCredentialData,
  RealTradingStatusData,
  RuntimeTradingConfigData,
  TradingEnvironment,
} from '../types';

type Section = 'cuenta' | 'ia' | 'alertas' | 'proteccion';

const SECTIONS: ReadonlyArray<{ id: Section; label: MessageKey }> = [
  { id: 'cuenta', label: 'settings.nav.account' },
  { id: 'ia', label: 'settings.nav.ai' },
  { id: 'alertas', label: 'settings.nav.alerts' },
  { id: 'proteccion', label: 'settings.nav.protection' },
];

export function SettingsView({
  section,
  navigate,
  runtime,
}: {
  section: string | null;
  navigate: (page: Page, section?: string) => void;
  runtime: PollingState<RuntimeTradingConfigData>;
}) {
  const { t } = useI18n();
  const active: Section = SECTIONS.some((item) => item.id === section) ? (section as Section) : 'cuenta';
  const integrations = useIntegrations();

  return (
    <>
      <PageHead title={t('settings.title')} description={t('settings.description')} />
      <SubNav
        label={t('settings.nav.label')}
        items={SECTIONS.map((item) => ({ id: item.id, label: t(item.label) }))}
        value={active}
        onChange={(id) => navigate('settings', id)}
      />

      {active === 'cuenta' && (
        <div className="stack-lg">
          <AccountBalanceCard />
          <ExchangeKeys runtime={runtime} />
        </div>
      )}
      {active === 'ia' && <LlmSettings integrations={integrations} />}
      {active === 'alertas' && (
        <div className="stack-lg">
          <AlertSettings integrations={integrations} />
          <AlertChannels />
        </div>
      )}
      {active === 'proteccion' && (
        <div className="stack-lg">
          <RuntimeLimits runtime={runtime} />
          <RealTradingStatus />
        </div>
      )}
    </>
  );
}

/* ------------------------------------------------------------ Exchange */

function ExchangeKeys({ runtime }: { runtime: PollingState<RuntimeTradingConfigData> }) {
  const { t } = useI18n();
  const credentials = usePolling<EncryptedCredentialData[]>('vault', () =>
    apiGet<EncryptedCredentialData[]>('/api/v1/vault/status'),
  );
  const [apiKey, setApiKey] = useState('');
  const [apiSecret, setApiSecret] = useState('');
  const [environment, setEnvironment] = useState<TradingEnvironment>(runtime.data?.trading_environment ?? 'testnet');
  const [busy, setBusy] = useState<string | null>(null);
  const [ping, setPing] = useState<ConnectionPingResult | null>(null);

  const save = async () => {
    if (!apiKey.trim() || !apiSecret.trim()) {
      toast.error(t('keys.toast.missing'));
      return;
    }
    setBusy('save');
    try {
      await apiSend('POST', '/api/v1/vault/keys', {
        exchange: 'binance',
        api_key: apiKey.trim(),
        api_secret: apiSecret.trim(),
        is_testnet: environment === 'testnet',
      });
      setApiKey('');
      setApiSecret('');
      await Promise.all([credentials.refresh(), runtime.refresh()]);
      toast.success(t('keys.toast.saved'));
    } catch (error) {
      toast.error(t('common.saveFailed', { detail: errorMessage(error) }));
    } finally {
      setBusy(null);
    }
  };

  const test = async () => {
    setBusy('test');
    try {
      const result = await apiSend<ConnectionPingResult>('POST', '/api/v1/vault/test-connection', {
        exchange: 'binance',
        is_testnet: environment === 'testnet',
      });
      setPing(result);
      await credentials.refresh();
      toast.success(t('keys.toast.connected', { ms: result.latency_ms }));
    } catch (error) {
      setPing(null);
      toast.error(t('keys.toast.failed', { detail: errorMessage(error) }));
    } finally {
      setBusy(null);
    }
  };

  const yesNo = (value: boolean | null) => (value ? t('common.yes') : t('common.no'));

  return (
    <Card title={t('keys.title')} icon="key" subtitle={t('keys.subtitle')}>
      <div className="stack">
        <div className="field">
          <span className="field-label">{t('keys.network')}</span>
          <Segmented
            label={t('keys.network')}
            value={environment}
            onChange={setEnvironment}
            options={[
              { value: 'testnet', label: t('keys.network.testnet') },
              { value: 'mainnet', label: t('keys.network.mainnet') },
            ]}
          />
          <span className="field-hint">{t('keys.network.hint')}</span>
        </div>
        <div className="form-grid">
          <label className="field">
            <span className="field-label">API Key</span>
            <input
              className="input"
              autoComplete="off"
              spellCheck={false}
              placeholder={t('keys.apiKey.placeholder')}
              value={apiKey}
              onChange={(event) => setApiKey(event.target.value)}
            />
          </label>
          <label className="field">
            <span className="field-label">Secret Key</span>
            <input
              className="input"
              type="password"
              autoComplete="off"
              placeholder={t('keys.secret.placeholder')}
              value={apiSecret}
              onChange={(event) => setApiSecret(event.target.value)}
            />
          </label>
        </div>
        <div className="row">
          <button type="button" className="btn btn-primary" disabled={busy !== null} onClick={() => void save()}>
            {busy === 'save' ? t('keys.encrypting') : t('keys.save')}
          </button>
          <button type="button" className="btn" disabled={busy !== null} onClick={() => void test()}>
            {busy === 'test' ? t('keys.testing') : t('keys.test')}
          </button>
        </div>

        {ping && (
          <div className="stack" style={{ gap: 8 }}>
            <Notice tone="ok">
              {t('keys.connected', {
                network: ping.is_testnet ? 'Testnet' : 'Mainnet',
                ms: ping.latency_ms,
              })}
              {ping.permissions_checked &&
                ` · ${t('keys.permissions', {
                  trading: yesNo(ping.can_trade),
                  withdrawals: yesNo(ping.withdrawals_enabled),
                  ip: ping.ip_restricted ? t('keys.ip.restricted') : t('keys.ip.open'),
                })}`}
            </Notice>
            {ping.withdrawals_enabled && <Notice tone="error">{t('keys.warn.withdrawals')}</Notice>}
            {ping.permissions_checked && !ping.ip_restricted && <Notice tone="warn">{t('keys.warn.ip')}</Notice>}
            {ping.permissions_checked && !ping.can_trade && <Notice tone="warn">{t('keys.warn.trading')}</Notice>}
            {!ping.permissions_checked && !ping.is_testnet && ping.warnings.length > 0 && (
              <Notice tone="warn">{ping.warnings.join(' ')}</Notice>
            )}
          </div>
        )}

        {(credentials.data?.length ?? 0) > 0 && (
          <div className="stack" style={{ gap: 8 }}>
            <span className="section-label">{t('keys.saved')}</span>
            <div className="row">
              {credentials.data?.map((item) => (
                <Pill
                  key={`${item.exchange}-${item.masked_key}-${item.is_testnet}`}
                  tone={item.has_withdrawal_permission ? 'neg' : 'default'}
                  title={
                    item.permissions_verified_at
                      ? t('keys.badge.verifiedHint')
                      : item.is_testnet
                        ? t('keys.badge.testnetHint')
                        : t('keys.badge.unverifiedHint')
                  }
                >
                  <span className="mono">{item.masked_key}</span> · {item.is_testnet ? 'Testnet' : 'Mainnet'}
                  {!item.is_testnet &&
                    ` · ${
                      item.permissions_verified_at
                        ? item.has_withdrawal_permission
                          ? t('keys.badge.withdrawals')
                          : t('keys.badge.noWithdrawals')
                        : t('keys.badge.unverified')
                    }`}
                </Pill>
              ))}
            </div>
          </div>
        )}
      </div>
    </Card>
  );
}

/* --------------------------------------------------------------- Limits */

function RuntimeLimits({ runtime }: { runtime: PollingState<RuntimeTradingConfigData> }) {
  const { t } = useI18n();
  if (!runtime.data) {
    return runtime.error ? (
      <Notice tone="error">{t('common.loadFailed', { detail: runtime.error })}</Notice>
    ) : (
      <Card>
        <Skeleton height={160} />
      </Card>
    );
  }
  return <RuntimeLimitsForm key={JSON.stringify(runtime.data)} config={runtime.data} onSaved={runtime.setData} />;
}

function RuntimeLimitsForm({
  config,
  onSaved,
}: {
  config: RuntimeTradingConfigData;
  onSaved: (value: RuntimeTradingConfigData) => void;
}) {
  const { t } = useI18n();
  const [maxNotional, setMaxNotional] = useState(String(config.real_trading_max_notional));
  const [symbols, setSymbols] = useState(config.real_trading_allowed_symbols.join(', '));
  const [autonomousReal, setAutonomousReal] = useState(config.enable_autonomous_real_trading);
  const [oco, setOco] = useState(config.real_trading_oco_enabled);
  const [busy, setBusy] = useState(false);

  const save = async (changes: Partial<RuntimeTradingConfigData>, confirm = false) => {
    setBusy(true);
    try {
      onSaved(await apiSend<RuntimeTradingConfigData>('PUT', '/api/v1/trading/runtime-config', { ...changes, confirm }));
      toast.success(t('common.saved'));
    } catch (error) {
      toast.error(t('common.saveFailed', { detail: errorMessage(error) }));
    } finally {
      setBusy(false);
    }
  };

  const switchEnvironment = (environment: TradingEnvironment) => {
    if (environment === config.trading_environment) return;
    if (environment === 'mainnet') {
      if (!config.credentials.mainnet) {
        toast.error(t('limits.toast.needMainnetKeys'));
        return;
      }
      if (!window.confirm(t('limits.confirmMainnet'))) return;
    }
    void save({ trading_environment: environment }, environment === 'mainnet');
  };

  const keyState = (present: boolean) => (present ? t('limits.keys.saved') : t('limits.keys.missing'));

  return (
    <Card title={t('limits.title')} icon="shield" subtitle={t('limits.subtitle')}>
      <div className="stack">
        <div className="field">
          <span className="field-label">{t('limits.network')}</span>
          <Segmented
            label={t('limits.network')}
            value={config.trading_environment}
            disabled={busy}
            onChange={switchEnvironment}
            options={[
              { value: 'testnet', label: t('limits.network.testnet') },
              { value: 'mainnet', label: t('keys.network.mainnet') },
            ]}
          />
          <span className="field-hint">
            {t('limits.keys', {
              testnet: keyState(Boolean(config.credentials.testnet)),
              mainnet: keyState(Boolean(config.credentials.mainnet)),
            })}
          </span>
        </div>

        {config.trading_environment === 'mainnet' && <Notice tone="error">{t('limits.mainnetActive')}</Notice>}

        <div className="form-grid">
          <label className="field">
            <span className="field-label">{t('limits.maxOrder')}</span>
            <input
              className="input"
              type="number"
              min={1}
              step={1}
              value={maxNotional}
              onChange={(event) => setMaxNotional(event.target.value)}
            />
            <span className="field-hint">{t('limits.maxOrder.hint')}</span>
          </label>
          <label className="field">
            <span className="field-label">{t('limits.markets')}</span>
            <input
              className="input"
              placeholder="BTC/USDT, ETH/USDT"
              value={symbols}
              onChange={(event) => setSymbols(event.target.value)}
            />
            <span className="field-hint">{t('limits.markets.hint')}</span>
          </label>
        </div>

        <div className="stack" style={{ gap: 4 }}>
          <label className="check">
            <input
              type="checkbox"
              checked={config.allow_mainnet_trading}
              disabled={busy}
              onChange={(event) => void save({ allow_mainnet_trading: event.target.checked }, event.target.checked)}
            />
            <span>
              {t('limits.allowMainnet')}
              <span className="check-hint">{t('limits.allowMainnet.hint')}</span>
            </span>
          </label>
          <label className="check">
            <input type="checkbox" checked={autonomousReal} onChange={(event) => setAutonomousReal(event.target.checked)} />
            <span>
              {t('limits.autonomousReal')}
              <span className="check-hint">{t('limits.autonomousReal.hint')}</span>
            </span>
          </label>
          <label className="check">
            <input type="checkbox" checked={oco} onChange={(event) => setOco(event.target.checked)} />
            <span>
              {t('limits.oco')}
              <span className="check-hint">{t('limits.oco.hint')}</span>
            </span>
          </label>
        </div>

        <div className="row">
          <button
            type="button"
            className="btn btn-primary"
            disabled={busy}
            onClick={() =>
              void save({
                real_trading_max_notional: Number(maxNotional),
                real_trading_allowed_symbols: symbols
                  .split(',')
                  .map((item) => item.trim().toUpperCase())
                  .filter(Boolean),
                enable_autonomous_real_trading: autonomousReal,
                real_trading_oco_enabled: oco,
                require_manual_confirmation: true,
              })
            }
          >
            {busy ? t('common.saving') : t('limits.save')}
          </button>
        </div>
      </div>
    </Card>
  );
}

const PROTECTION_KEY: Record<string, MessageKey> = {
  exchange_oco: 'real.protection.oco',
  oco_with_local_fallback: 'real.protection.ocoLocal',
  oco_discovery_failed: 'real.protection.failed',
  local_guard: 'real.protection.local',
};

function RealTradingStatus() {
  const { t } = useI18n();
  const status = usePolling<RealTradingStatusData>(
    'real-status',
    () => apiGet<RealTradingStatusData>('/api/v1/trading/real/status'),
    15000,
  );
  const [busy, setBusy] = useState(false);

  const reconcile = async () => {
    setBusy(true);
    try {
      const result = await apiSend<{ removed?: string[] }>('POST', '/api/v1/trading/real/reconcile');
      await status.refresh();
      const removed = result.removed ?? [];
      toast.success(
        removed.length ? t('real.toast.reconciledRemoved', { list: removed.join(', ') }) : t('real.toast.reconciled'),
      );
    } catch (error) {
      toast.error(t('real.toast.reconcileFailed', { detail: errorMessage(error) }));
    } finally {
      setBusy(false);
    }
  };

  const data = status.data;
  return (
    <Card
      title={t('real.title')}
      subtitle={t('real.subtitle')}
      actions={
        <>
          {data && <Pill tone={data.enabled ? 'solid' : 'default'}>{data.enabled ? t('real.enabled') : t('real.paperOnly')}</Pill>}
          <button type="button" className="btn btn-sm" disabled={busy} onClick={() => void reconcile()}>
            {busy ? t('real.syncing') : t('real.sync')}
          </button>
        </>
      }
    >
      {!data ? (
        status.error ? (
          <Notice tone="error">{status.error}</Notice>
        ) : (
          <Skeleton height={80} />
        )
      ) : (
        <div className="stack">
          <div className="stats">
            <Stat label={t('real.network')} value={data.testnet ? 'Testnet' : 'Mainnet'} tone={data.testnet ? '' : 'neg'} />
            <Stat
              label={t('real.keys')}
              value={data.credentials_configured ? t('real.keys.ready') : t('real.keys.missing')}
              tone={data.credentials_configured ? '' : 'neg'}
            />
            <Stat label={t('real.maxOrder')} value={fmtUsd(data.max_notional)} />
            <Stat label={t('real.positions')} value={data.open_positions} />
            <Stat
              label={t('real.guard')}
              value={data.guard_running ? t('common.active') : t('common.inactive')}
              sub={t(PROTECTION_KEY[data.protection_mode ?? ''] ?? 'real.protection.local')}
            />
            <Stat
              label={t('real.oco')}
              value={data.oco_enabled ? (data.oco_protected_positions ?? 0) : t('common.disabled')}
            />
          </div>
          <p className="muted xsmall">
            {t('real.summary', {
              markets: data.allowed_symbols.join(', ') || t('real.noRestriction'),
              autonomous: data.autonomous_real_enabled ? t('real.allowed') : t('real.notAllowed'),
            })}
          </p>
          {data.oco_discovery_failed_exchanges?.length ? (
            <Notice tone="error">{t('real.ocoFailed', { list: data.oco_discovery_failed_exchanges.join(', ') })}</Notice>
          ) : null}
        </div>
      )}
    </Card>
  );
}

/* --------------------------------------------------------------- Alerts */

function AlertChannels() {
  const { t } = useI18n();
  const status = usePolling<AlertStatusData>('alert-status', () => apiGet<AlertStatusData>('/api/v1/alerts/status'));
  if (!status.data) return null;
  return (
    <Card title={t('channels.title')} subtitle={t('channels.subtitle')}>
      <div className="row">
        <Pill tone={status.data.enabled ? 'solid' : 'default'}>
          {status.data.enabled ? t('channels.enabled') : t('channels.disabled')}
        </Pill>
        {status.data.channels.map((channel) => (
          <Pill key={channel.name}>
            {channel.name} · {channel.configured ? t('channels.ready') : t('channels.notConfigured')}
          </Pill>
        ))}
        <span className="muted xsmall num">
          {t('channels.counts', { sent: status.data.sent_count, failed: status.data.failed_count })}
        </span>
      </div>
    </Card>
  );
}
