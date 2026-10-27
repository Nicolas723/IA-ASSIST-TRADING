import { useState } from 'react';
import toast from 'react-hot-toast';
import type { Page } from '../App';
import AccountBalanceCard from '../components/AccountBalanceCard';
import { AlertSettings, LlmSettings } from '../components/IntegrationsSettings';
import { useIntegrations } from '../hooks/useIntegrations';
import { Card, Notice, PageHead, Pill, Segmented, Skeleton, Stat, SubNav } from '../components/ui';
import { usePolling, type PollingState } from '../hooks/usePolling';
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

const SECTIONS: ReadonlyArray<{ id: Section; label: string }> = [
  { id: 'cuenta', label: 'Cuenta y exchange' },
  { id: 'ia', label: 'Inteligencia artificial' },
  { id: 'alertas', label: 'Alertas' },
  { id: 'proteccion', label: 'Límites y protección' },
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
  const active: Section = SECTIONS.some((item) => item.id === section) ? (section as Section) : 'cuenta';
  const integrations = useIntegrations();

  return (
    <>
      <PageHead
        title="Configuración"
        description="Conecta tu exchange y tus servicios. Todo se guarda cifrado y se aplica al momento, sin reiniciar."
      />
      <SubNav label="Secciones de configuración" items={SECTIONS} value={active} onChange={(id) => navigate('settings', id)} />

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
  const credentials = usePolling<EncryptedCredentialData[]>('vault', () =>
    apiGet<EncryptedCredentialData[]>('/api/v1/vault/status'),
  );
  const [apiKey, setApiKey] = useState('');
  const [apiSecret, setApiSecret] = useState('');
  const [environment, setEnvironment] = useState<TradingEnvironment>(
    runtime.data?.trading_environment ?? 'testnet',
  );
  const [busy, setBusy] = useState<string | null>(null);
  const [ping, setPing] = useState<ConnectionPingResult | null>(null);

  const save = async () => {
    if (!apiKey.trim() || !apiSecret.trim()) {
      toast.error('Pega la API Key y la Secret Key.');
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
      toast.success('Claves guardadas y cifradas.');
    } catch (error) {
      toast.error(`No se pudieron guardar: ${errorMessage(error)}`);
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
      toast.success(`Conexión correcta · ${result.latency_ms} ms`);
    } catch (error) {
      setPing(null);
      toast.error(`Sin conexión: ${errorMessage(error)}`);
    } finally {
      setBusy(null);
    }
  };

  return (
    <Card
      title="Conexión con Binance"
      icon="key"
      subtitle="Solo hace falta para operar en Testnet o con dinero real. Usa claves sin permiso de retirada."
    >
      <div className="stack">
        <div className="field">
          <span className="field-label">Red</span>
          <Segmented
            label="Red de Binance"
            value={environment}
            onChange={setEnvironment}
            options={[
              { value: 'testnet', label: 'Testnet · pruebas' },
              { value: 'mainnet', label: 'Mainnet · dinero real' },
            ]}
          />
          <span className="field-hint">
            Las claves de cada red se guardan por separado. Testnet se crea gratis en testnet.binance.vision.
          </span>
        </div>
        <div className="form-grid">
          <label className="field">
            <span className="field-label">API Key</span>
            <input
              className="input"
              autoComplete="off"
              spellCheck={false}
              placeholder="Pega tu API Key"
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
              placeholder="Pega tu Secret Key"
              value={apiSecret}
              onChange={(event) => setApiSecret(event.target.value)}
            />
          </label>
        </div>
        <div className="row">
          <button type="button" className="btn btn-primary" disabled={busy !== null} onClick={() => void save()}>
            {busy === 'save' ? 'Cifrando…' : 'Guardar claves'}
          </button>
          <button type="button" className="btn" disabled={busy !== null} onClick={() => void test()}>
            {busy === 'test' ? 'Comprobando…' : 'Probar conexión'}
          </button>
        </div>

        {ping && (
          <div className="stack" style={{ gap: 8 }}>
            <Notice tone="ok">
              Conectado a Binance {ping.is_testnet ? 'Testnet' : 'Mainnet'} · latencia {ping.latency_ms} ms
              {ping.permissions_checked &&
                ` · trading ${ping.can_trade ? 'sí' : 'no'} · retiradas ${ping.withdrawals_enabled ? 'SÍ' : 'no'} · IP ${ping.ip_restricted ? 'restringida' : 'sin restringir'}`}
            </Notice>
            {ping.warnings.map((warning) => (
              <Notice key={warning} tone={warning.includes('RETIRADAS') ? 'error' : 'warn'}>
                {warning}
              </Notice>
            ))}
          </div>
        )}

        {(credentials.data?.length ?? 0) > 0 && (
          <div className="stack" style={{ gap: 8 }}>
            <span className="section-label">Claves guardadas</span>
            <div className="row">
              {credentials.data?.map((item) => (
                <Pill
                  key={`${item.exchange}-${item.masked_key}-${item.is_testnet}`}
                  tone={item.has_withdrawal_permission ? 'neg' : 'default'}
                  title={
                    item.permissions_verified_at
                      ? 'Permisos confirmados por Binance'
                      : item.is_testnet
                        ? 'Testnet no informa de permisos'
                        : 'Pulsa «Probar conexión» para verificar los permisos'
                  }
                >
                  <span className="mono">{item.masked_key}</span> · {item.is_testnet ? 'Testnet' : 'Mainnet'}
                  {!item.is_testnet &&
                    (item.permissions_verified_at
                      ? item.has_withdrawal_permission
                        ? ' · permite retiradas'
                        : ' · sin retiradas'
                      : ' · sin verificar')}
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
  if (!runtime.data) {
    return runtime.error ? (
      <Notice tone="error">No se pudo leer la configuración: {runtime.error}</Notice>
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
  const [maxNotional, setMaxNotional] = useState(String(config.real_trading_max_notional));
  const [symbols, setSymbols] = useState(config.real_trading_allowed_symbols.join(', '));
  const [autonomousReal, setAutonomousReal] = useState(config.enable_autonomous_real_trading);
  const [oco, setOco] = useState(config.real_trading_oco_enabled);
  const [busy, setBusy] = useState(false);

  const save = async (changes: Partial<RuntimeTradingConfigData>, confirm = false) => {
    setBusy(true);
    try {
      onSaved(await apiSend<RuntimeTradingConfigData>('PUT', '/api/v1/trading/runtime-config', { ...changes, confirm }));
      toast.success('Configuración guardada.');
    } catch (error) {
      toast.error(`No se pudo guardar: ${errorMessage(error)}`);
    } finally {
      setBusy(false);
    }
  };

  const switchEnvironment = (environment: TradingEnvironment) => {
    if (environment === config.trading_environment) return;
    if (environment === 'mainnet') {
      if (!config.credentials.mainnet) {
        toast.error('Primero guarda tus claves de Mainnet en «Cuenta y exchange».');
        return;
      }
      if (!window.confirm('Vas a cambiar a Mainnet: las órdenes reales usarán tu dinero. ¿Continuar?')) return;
    }
    void save({ trading_environment: environment }, environment === 'mainnet');
  };

  return (
    <Card
      title="Límites de dinero real"
      icon="shield"
      subtitle="Se aplican a cualquier orden real, la envíe el asistente o tú. La confirmación por orden siempre está activa."
    >
      <div className="stack">
        <div className="field">
          <span className="field-label">Red activa</span>
          <Segmented
            label="Red activa"
            value={config.trading_environment}
            disabled={busy}
            onChange={switchEnvironment}
            options={[
              { value: 'testnet', label: 'Testnet (recomendado)' },
              { value: 'mainnet', label: 'Mainnet · dinero real' },
            ]}
          />
          <span className="field-hint">
            Claves de Testnet: {config.credentials.testnet ? 'guardadas' : 'faltan'} · Mainnet:{' '}
            {config.credentials.mainnet ? 'guardadas' : 'faltan'}
          </span>
        </div>

        {config.trading_environment === 'mainnet' && (
          <Notice tone="error">Mainnet está activo: las órdenes confirmadas usan dinero real.</Notice>
        )}

        <div className="form-grid">
          <label className="field">
            <span className="field-label">Máximo por orden (USDT)</span>
            <input
              className="input"
              type="number"
              min={1}
              step={1}
              value={maxNotional}
              onChange={(event) => setMaxNotional(event.target.value)}
            />
            <span className="field-hint">Ninguna orden real superará esta cantidad.</span>
          </label>
          <label className="field">
            <span className="field-label">Mercados permitidos</span>
            <input
              className="input"
              placeholder="BTC/USDT, ETH/USDT"
              value={symbols}
              onChange={(event) => setSymbols(event.target.value)}
            />
            <span className="field-hint">Separados por comas. Fuera de esta lista no se abre nada real.</span>
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
              Permitir dinero real (Mainnet)
              <span className="check-hint">Segundo permiso obligatorio además de tener las claves.</span>
            </span>
          </label>
          <label className="check">
            <input type="checkbox" checked={autonomousReal} onChange={(event) => setAutonomousReal(event.target.checked)} />
            <span>
              Permitir que el asistente opere solo con dinero real
              <span className="check-hint">Si está desactivado, el asistente solo puede funcionar en simulado.</span>
            </span>
          </label>
          <label className="check">
            <input type="checkbox" checked={oco} onChange={(event) => setOco(event.target.checked)} />
            <span>
              Proteger posiciones en el propio exchange (OCO)
              <span className="check-hint">Stop y objetivo quedan en Binance aunque este ordenador se apague.</span>
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
            {busy ? 'Guardando…' : 'Guardar límites'}
          </button>
        </div>
      </div>
    </Card>
  );
}

const PROTECTION_LABEL: Record<string, string> = {
  exchange_oco: 'OCO en el exchange',
  oco_with_local_fallback: 'OCO + vigilancia local',
  oco_discovery_failed: 'OCO sin confirmar · ventas bloqueadas',
  local_guard: 'Vigilancia local',
};

function RealTradingStatus() {
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
      toast.success(removed.length ? `Sincronizado. Cerradas fuera: ${removed.join(', ')}` : 'Posiciones sincronizadas.');
    } catch (error) {
      toast.error(`No se pudo sincronizar: ${errorMessage(error)}`);
    } finally {
      setBusy(false);
    }
  };

  const data = status.data;
  return (
    <Card
      title="Estado del trading real"
      subtitle="Comprobaciones en vivo de credenciales, protecciones y posiciones."
      actions={
        <>
          {data && <Pill tone={data.enabled ? 'solid' : 'default'}>{data.enabled ? 'Habilitado' : 'Solo simulado'}</Pill>}
          <button type="button" className="btn btn-sm" disabled={busy} onClick={() => void reconcile()}>
            {busy ? 'Sincronizando…' : 'Sincronizar con Binance'}
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
            <Stat label="Red" value={data.testnet ? 'Testnet' : 'Mainnet'} tone={data.testnet ? '' : 'neg'} />
            <Stat label="Claves" value={data.credentials_configured ? 'Listas' : 'Faltan'} tone={data.credentials_configured ? '' : 'neg'} />
            <Stat label="Máx. por orden" value={fmtUsd(data.max_notional)} />
            <Stat label="Posiciones reales" value={data.open_positions} />
            <Stat
              label="Vigilancia de stops"
              value={data.guard_running ? 'Activa' : 'Inactiva'}
              sub={PROTECTION_LABEL[data.protection_mode ?? ''] ?? 'Vigilancia local'}
            />
            <Stat label="Posiciones con OCO" value={data.oco_enabled ? (data.oco_protected_positions ?? 0) : 'Desactivado'} />
          </div>
          <p className="muted xsmall">
            Mercados permitidos: {data.allowed_symbols.join(', ') || 'sin restricción'} · Asistente con dinero real:{' '}
            {data.autonomous_real_enabled ? 'permitido' : 'no permitido'}
          </p>
          {data.oco_discovery_failed_exchanges?.length ? (
            <Notice tone="error">
              No se pudo confirmar la protección OCO en {data.oco_discovery_failed_exchanges.join(', ')}. Las ventas
              quedan bloqueadas hasta comprobarlo para evitar una venta doble.
            </Notice>
          ) : null}
        </div>
      )}
    </Card>
  );
}

/* --------------------------------------------------------------- Alerts */

function AlertChannels() {
  const status = usePolling<AlertStatusData>('alert-status', () => apiGet<AlertStatusData>('/api/v1/alerts/status'));
  if (!status.data) return null;
  return (
    <Card title="Canales de aviso" subtitle="Webhook y email se configuran en el servidor; aquí ves su estado.">
      <div className="row">
        <Pill tone={status.data.enabled ? 'solid' : 'default'}>{status.data.enabled ? 'Alertas activas' : 'Alertas desactivadas'}</Pill>
        {status.data.channels.map((channel) => (
          <Pill key={channel.name}>
            {channel.name} · {channel.configured ? 'listo' : 'sin configurar'}
          </Pill>
        ))}
        <span className="muted xsmall num">
          Enviadas {status.data.sent_count} · fallidas {status.data.failed_count}
        </span>
      </div>
    </Card>
  );
}
