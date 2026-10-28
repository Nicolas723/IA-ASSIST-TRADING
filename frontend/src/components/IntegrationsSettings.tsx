import { useState } from 'react';
import toast from 'react-hot-toast';
import type { ChainEntry, IntegrationsData, ModelOption, ProviderId, ProviderView } from '../hooks/useIntegrations';
import type { PollingState } from '../hooks/usePolling';
import { useI18n } from '../i18n/core';
import { apiGet, apiSend, errorMessage } from '../lib/api';
import { Card, Disclosure, Notice, Pill, Skeleton } from './ui';

interface ProviderDraft {
  api_key: string;
  model: string;
  base_url: string;
  enabled: boolean;
  clear_key: boolean;
}

function draftsFrom(data: IntegrationsData): Record<string, ProviderDraft> {
  return Object.fromEntries(
    data.llm.providers.map((provider) => [
      provider.provider,
      {
        api_key: '',
        model: provider.model,
        base_url: provider.base_url ?? '',
        enabled: provider.enabled,
        clear_key: false,
      },
    ]),
  );
}

function LoadingOrError({ integrations, height }: { integrations: PollingState<IntegrationsData>; height: number }) {
  const { t } = useI18n();
  return integrations.error ? (
    <Notice tone="error">{t('common.loadFailed', { detail: integrations.error })}</Notice>
  ) : (
    <Card>
      <Skeleton height={height} />
    </Card>
  );
}

/* ----------------------------------------------------------------- LLM */

export function LlmSettings({ integrations }: { integrations: PollingState<IntegrationsData> }) {
  const data = integrations.data;
  if (!data) return <LoadingOrError integrations={integrations} height={180} />;
  // Remount the form whenever a save returns fresh data, so drafts reset.
  return <LlmForm key={JSON.stringify(data.llm)} data={data} onSaved={integrations.setData} />;
}

function LlmForm({ data, onSaved }: { data: IntegrationsData; onSaved: (value: IntegrationsData) => void }) {
  const { t } = useI18n();
  const [drafts, setDrafts] = useState(() => draftsFrom(data));
  const [chain, setChain] = useState<ChainEntry[]>(data.llm.model_chain);
  const [tests, setTests] = useState<Record<string, boolean>>({});
  const [busy, setBusy] = useState<string | null>(null);

  const providers = data.llm.providers;
  const updateDraft = (provider: ProviderId, patch: Partial<ProviderDraft>) =>
    setDrafts((current) => ({ ...current, [provider]: { ...current[provider], ...patch } }));

  const save = async () => {
    setBusy('save');
    try {
      const snapshot = await apiSend<IntegrationsData>('PUT', '/api/v1/settings/llm', {
        providers: providers.map((provider) => {
          const draft = drafts[provider.provider];
          return {
            provider: provider.provider,
            api_key: draft.api_key.trim() || null,
            clear_key: draft.clear_key,
            model: draft.model,
            base_url: provider.supports_base_url ? draft.base_url : null,
            enabled: draft.enabled,
          };
        }),
        model_chain: chain,
      });
      onSaved(snapshot);
      toast.success(t('llm.toast.saved'));
    } catch (error) {
      toast.error(t('common.saveFailed', { detail: errorMessage(error) }));
    } finally {
      setBusy(null);
    }
  };

  const test = async (provider: ProviderId) => {
    setBusy(`test-${provider}`);
    try {
      const result = await apiSend<{ providers: Record<string, boolean> }>('POST', '/api/v1/settings/llm/test', {
        provider,
      });
      setTests((current) => ({ ...current, ...result.providers }));
    } catch (error) {
      toast.error(t('common.testFailed', { detail: errorMessage(error) }));
    } finally {
      setBusy(null);
    }
  };

  const configuredIds = new Set(providers.filter((provider) => provider.configured).map((p) => p.provider));
  const inUse = chain.filter((entry) => entry.enabled && configuredIds.has(entry.provider)).length;

  return (
    <div className="stack-lg">
      <Card
        title={t('llm.chain.title')}
        subtitle={t('llm.chain.subtitle')}
        actions={
          <Pill tone={inUse ? 'solid' : 'default'}>
            {inUse ? t('llm.chain.active', { count: inUse }) : t('llm.chain.none')}
          </Pill>
        }
      >
        <ModelChainEditor providers={providers} chain={chain} onChange={setChain} />
      </Card>

      <div className="cols">
        {providers.map((provider) => {
          const draft = drafts[provider.provider];
          const result = tests[provider.provider];
          return (
            <Card
              key={provider.provider}
              title={provider.label}
              actions={
                <>
                  {result !== undefined && (
                    <Pill tone={result ? 'pos' : 'neg'}>{result ? t('llm.test.ok') : t('llm.test.fail')}</Pill>
                  )}
                  <Pill tone={provider.configured ? 'solid' : 'default'}>
                    {provider.configured ? t('common.active') : t('llm.noKey')}
                  </Pill>
                </>
              }
            >
              <div className="stack">
                {provider.requires_key ? (
                  <label className="field">
                    <span className="field-label">{t('llm.apiKey')}</span>
                    <input
                      type="password"
                      className="input"
                      autoComplete="off"
                      disabled={draft.clear_key}
                      placeholder={
                        provider.masked_key ? t('common.storedSecret', { value: provider.masked_key }) : t('llm.apiKey.placeholder')
                      }
                      value={draft.api_key}
                      onChange={(event) => updateDraft(provider.provider, { api_key: event.target.value })}
                    />
                  </label>
                ) : (
                  <label className="check">
                    <input
                      type="checkbox"
                      checked={draft.enabled}
                      onChange={(event) => updateDraft(provider.provider, { enabled: event.target.checked })}
                    />
                    <span>{t('llm.ollama')}</span>
                  </label>
                )}
                <label className="field">
                  <span className="field-label">{t('llm.model')}</span>
                  <input
                    className="input"
                    value={draft.model}
                    onChange={(event) => updateDraft(provider.provider, { model: event.target.value })}
                  />
                </label>
                {provider.supports_base_url && (
                  <Disclosure summary={t('llm.baseUrl.section')}>
                    <label className="field">
                      <span className="field-label">{t('llm.baseUrl')}</span>
                      <input
                        className="input"
                        value={draft.base_url}
                        onChange={(event) => updateDraft(provider.provider, { base_url: event.target.value })}
                      />
                    </label>
                  </Disclosure>
                )}
                <div className="row-between">
                  <div className="row">
                    {provider.requires_key && provider.masked_key && (
                      <label className="check small">
                        <input
                          type="checkbox"
                          checked={draft.clear_key}
                          onChange={(event) =>
                            updateDraft(provider.provider, { clear_key: event.target.checked, api_key: '' })
                          }
                        />
                        <span>{t('llm.clearKey')}</span>
                      </label>
                    )}
                  </div>
                  <button
                    type="button"
                    className="btn btn-sm"
                    disabled={busy !== null || !provider.configured}
                    onClick={() => void test(provider.provider)}
                  >
                    {busy === `test-${provider.provider}` ? t('common.testing') : t('common.test')}
                  </button>
                </div>
              </div>
            </Card>
          );
        })}
      </div>

      <div className="row">
        <button type="button" className="btn btn-primary" disabled={busy !== null} onClick={() => void save()}>
          {busy === 'save' ? t('common.saving') : t('llm.save')}
        </button>
        <span className="muted xsmall">{t('llm.encryptedNote')}</span>
      </div>
    </div>
  );
}

/** Providers whose live catalogue the backend can list. */
const CATALOG_PROVIDERS = new Set<ProviderId>(['openrouter', 'groq', 'deepseek', 'openai']);

function ModelChainEditor({
  providers,
  chain,
  onChange,
}: {
  providers: ProviderView[];
  chain: ChainEntry[];
  onChange: (next: ChainEntry[]) => void;
}) {
  const { t } = useI18n();
  const byId = new Map(providers.map((provider) => [provider.provider, provider]));
  const firstConfigured = providers.find((provider) => provider.configured)?.provider ?? providers[0]?.provider;
  const [provider, setProvider] = useState<ProviderId>(firstConfigured ?? 'groq');
  const [model, setModel] = useState('');
  const [catalog, setCatalog] = useState<Partial<Record<ProviderId, ModelOption[]>>>({});
  const [loading, setLoading] = useState(false);
  const [freeOnly, setFreeOnly] = useState(true);

  const move = (index: number, delta: number) => {
    const target = index + delta;
    if (target < 0 || target >= chain.length) return;
    const next = [...chain];
    [next[index], next[target]] = [next[target], next[index]];
    onChange(next);
  };
  const update = (index: number, patch: Partial<ChainEntry>) =>
    onChange(chain.map((entry, position) => (position === index ? { ...entry, ...patch } : entry)));
  const remove = (index: number) => onChange(chain.filter((_, position) => position !== index));
  const add = () => {
    const entry: ChainEntry = { provider, model: model.trim(), enabled: true };
    if (!chain.some((item) => item.provider === entry.provider && item.model === entry.model)) {
      onChange([...chain, entry]);
    }
    setModel('');
  };
  const load = async () => {
    setLoading(true);
    try {
      const options = await apiGet<ModelOption[]>(`/api/v1/settings/llm/models/${provider}`);
      setCatalog((current) => ({ ...current, [provider]: options }));
    } catch (error) {
      toast.error(t('llm.order.loadFailed', { detail: errorMessage(error) }));
    } finally {
      setLoading(false);
    }
  };

  const loaded = catalog[provider];
  const options = (loaded ?? []).filter((option) => !freeOnly || option.free);
  const listId = `models-${provider}`;

  return (
    <div className="stack">
      {chain.length === 0 ? (
        <Notice tone="warn">{t('llm.order.empty')}</Notice>
      ) : (
        <ol className="chain-list">
          {chain.map((entry, index) => {
            const view = byId.get(entry.provider);
            const usable = view?.configured ?? false;
            return (
              <li key={`${entry.provider}:${entry.model}`} className={entry.enabled && usable ? '' : 'is-off'}>
                <span className="chain-pos num">{index + 1}</span>
                <div className="chain-body">
                  <strong>{view?.label ?? entry.provider}</strong>
                  <span className="mono xsmall">
                    {entry.model || t('llm.order.default', { model: view?.model || '—' })}
                  </span>
                </div>
                {!usable && <Pill>{t('llm.order.noKey')}</Pill>}
                <label className="check small">
                  <input
                    type="checkbox"
                    checked={entry.enabled}
                    onChange={(event) => update(index, { enabled: event.target.checked })}
                  />
                  <span>{t('llm.order.enabled')}</span>
                </label>
                <div className="row chain-actions">
                  <button
                    type="button"
                    className="btn btn-sm btn-ghost"
                    aria-label={t('llm.order.up')}
                    title={t('llm.order.up')}
                    disabled={index === 0}
                    onClick={() => move(index, -1)}
                  >
                    ↑
                  </button>
                  <button
                    type="button"
                    className="btn btn-sm btn-ghost"
                    aria-label={t('llm.order.down')}
                    title={t('llm.order.down')}
                    disabled={index === chain.length - 1}
                    onClick={() => move(index, 1)}
                  >
                    ↓
                  </button>
                  <button
                    type="button"
                    className="btn btn-sm btn-ghost"
                    aria-label={t('llm.order.remove')}
                    title={t('llm.order.remove')}
                    onClick={() => remove(index)}
                  >
                    ✕
                  </button>
                </div>
              </li>
            );
          })}
        </ol>
      )}

      <Disclosure summary={t('llm.order.addTitle')}>
        <div className="stack">
          <div className="form-grid">
            <label className="field">
              <span className="field-label">{t('llm.order.provider')}</span>
              <select
                className="select"
                value={provider}
                onChange={(event) => {
                  setProvider(event.target.value as ProviderId);
                  setModel('');
                }}
              >
                {providers.map((item) => (
                  <option key={item.provider} value={item.provider}>
                    {item.label}
                    {item.configured ? '' : ` (${t('llm.noKey')})`}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span className="field-label">{t('llm.order.model')}</span>
              <input
                className="input mono"
                list={listId}
                placeholder={t('llm.order.modelPlaceholder')}
                value={model}
                onChange={(event) => setModel(event.target.value)}
              />
              <datalist id={listId}>
                {options.map((option) => (
                  <option key={option.id} value={option.id}>
                    {option.free ? `${option.name} · ${t('llm.order.free')}` : option.name}
                  </option>
                ))}
              </datalist>
            </label>
          </div>
          <div className="row-between">
            <div className="row">
              {CATALOG_PROVIDERS.has(provider) && (
                <button type="button" className="btn btn-sm" disabled={loading} onClick={() => void load()}>
                  {loading ? t('llm.order.loading') : t('llm.order.loadModels')}
                </button>
              )}
              {loaded && (
                <>
                  <label className="check small">
                    <input type="checkbox" checked={freeOnly} onChange={(event) => setFreeOnly(event.target.checked)} />
                    <span>{t('llm.order.freeOnly')}</span>
                  </label>
                  <span className="muted xsmall">
                    {t('llm.order.loaded', {
                      free: loaded.filter((option) => option.free).length,
                      total: loaded.length,
                    })}
                  </span>
                </>
              )}
            </div>
            <button type="button" className="btn btn-sm btn-primary" onClick={add}>
              {t('llm.order.add')}
            </button>
          </div>
          <p className="muted xsmall">{t('llm.order.tip')}</p>
        </div>
      </Disclosure>
    </div>
  );
}

/* -------------------------------------------------------------- Alerts */

export function AlertSettings({ integrations }: { integrations: PollingState<IntegrationsData> }) {
  const data = integrations.data;
  if (!data) return <LoadingOrError integrations={integrations} height={160} />;
  return (
    <div className="stack-lg">
      <TelegramForm key={JSON.stringify(data.telegram)} data={data} onSaved={integrations.setData} />
      <HeartbeatForm key={JSON.stringify(data.heartbeat)} data={data} onSaved={integrations.setData} />
    </div>
  );
}

function TelegramForm({ data, onSaved }: { data: IntegrationsData; onSaved: (value: IntegrationsData) => void }) {
  const { t } = useI18n();
  const [token, setToken] = useState('');
  const [chatId, setChatId] = useState(data.telegram.chat_id);
  const [enabled, setEnabled] = useState(data.telegram.enabled);
  const [clearToken, setClearToken] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);

  const save = async () => {
    setBusy('save');
    try {
      onSaved(
        await apiSend<IntegrationsData>('PUT', '/api/v1/settings/telegram', {
          bot_token: token.trim() || null,
          clear_token: clearToken,
          chat_id: chatId,
          enabled,
        }),
      );
      toast.success(t('telegram.toast.saved'));
    } catch (error) {
      toast.error(t('common.saveFailed', { detail: errorMessage(error) }));
    } finally {
      setBusy(null);
    }
  };

  const test = async () => {
    setBusy('test');
    try {
      const result = await apiSend<{ delivered: boolean }>('POST', '/api/v1/settings/telegram/test');
      if (result.delivered) toast.success(t('telegram.toast.delivered'));
      else toast.error(t('telegram.toast.notDelivered'));
    } catch (error) {
      toast.error(t('common.testFailed', { detail: errorMessage(error) }));
    } finally {
      setBusy(null);
    }
  };

  return (
    <Card
      title="Telegram"
      icon="bell"
      subtitle={t('telegram.subtitle')}
      actions={
        <Pill tone={data.telegram.configured ? 'solid' : 'default'}>
          {data.telegram.configured ? t('common.configured') : t('common.notConfigured')}
        </Pill>
      }
    >
      <div className="stack">
        <div className="form-grid">
          <label className="field">
            <span className="field-label">{t('telegram.token')}</span>
            <input
              type="password"
              className="input"
              autoComplete="off"
              disabled={clearToken}
              placeholder={
                data.telegram.masked_token
                  ? t('common.storedSecret', { value: data.telegram.masked_token })
                  : t('telegram.token.placeholder')
              }
              value={token}
              onChange={(event) => setToken(event.target.value)}
            />
          </label>
          <label className="field">
            <span className="field-label">Chat id</span>
            <input
              className="input"
              placeholder={t('telegram.chat.placeholder')}
              value={chatId}
              onChange={(event) => setChatId(event.target.value)}
            />
          </label>
        </div>
        <div className="row">
          <label className="check">
            <input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} />
            <span>{t('telegram.enabled')}</span>
          </label>
          {data.telegram.masked_token && (
            <label className="check small">
              <input
                type="checkbox"
                checked={clearToken}
                onChange={(event) => {
                  setClearToken(event.target.checked);
                  if (event.target.checked) setToken('');
                }}
              />
              <span>{t('telegram.clear')}</span>
            </label>
          )}
        </div>
        <div className="row">
          <button type="button" className="btn btn-primary" disabled={busy !== null} onClick={() => void save()}>
            {busy === 'save' ? t('common.saving') : t('common.save')}
          </button>
          <button type="button" className="btn" disabled={busy !== null} onClick={() => void test()}>
            {busy === 'test' ? t('common.sending') : t('telegram.test')}
          </button>
        </div>
      </div>
    </Card>
  );
}

function HeartbeatForm({ data, onSaved }: { data: IntegrationsData; onSaved: (value: IntegrationsData) => void }) {
  const { t } = useI18n();
  const [url, setUrl] = useState('');
  const [interval, setIntervalSeconds] = useState(data.heartbeat.interval_seconds);
  const [disable, setDisable] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);

  const save = async () => {
    setBusy('save');
    try {
      const snapshot = await apiSend<IntegrationsData>('PUT', '/api/v1/settings/heartbeat', {
        ping_url: url.trim() || null,
        clear_url: disable,
        interval_seconds: interval,
      });
      onSaved(snapshot);
      toast.success(snapshot.heartbeat.running ? t('heartbeat.toast.on') : t('heartbeat.toast.off'));
    } catch (error) {
      toast.error(t('common.saveFailed', { detail: errorMessage(error) }));
    } finally {
      setBusy(null);
    }
  };

  const test = async () => {
    setBusy('test');
    try {
      const result = await apiSend<{ ok: boolean }>('POST', '/api/v1/settings/heartbeat/test');
      if (result.ok) toast.success(t('heartbeat.toast.pinged'));
      else toast.error(t('heartbeat.toast.rejected'));
    } catch (error) {
      toast.error(t('common.testFailed', { detail: errorMessage(error) }));
    } finally {
      setBusy(null);
    }
  };

  return (
    <Card
      title={t('heartbeat.title')}
      icon="heart"
      subtitle={t('heartbeat.subtitle')}
      actions={
        <Pill tone={data.heartbeat.running ? 'solid' : 'default'}>
          {data.heartbeat.running
            ? t('heartbeat.running')
            : data.heartbeat.configured
              ? t('heartbeat.stopped')
              : t('common.disabled')}
        </Pill>
      }
    >
      <div className="stack">
        <div className="form-grid">
          <label className="field">
            <span className="field-label">{t('heartbeat.url')}</span>
            <input
              type="password"
              className="input"
              autoComplete="off"
              disabled={disable}
              placeholder={
                data.heartbeat.masked_url ? t('common.storedSecret', { value: data.heartbeat.masked_url }) : 'https://hc-ping.com/…'
              }
              value={url}
              onChange={(event) => setUrl(event.target.value)}
            />
          </label>
          <label className="field">
            <span className="field-label">{t('heartbeat.interval')}</span>
            <input
              className="input"
              type="number"
              min={15}
              max={3600}
              value={interval}
              onChange={(event) => setIntervalSeconds(Number(event.target.value))}
            />
          </label>
        </div>
        {data.heartbeat.configured && (
          <label className="check small">
            <input type="checkbox" checked={disable} onChange={(event) => setDisable(event.target.checked)} />
            <span>{t('heartbeat.disable')}</span>
          </label>
        )}
        <div className="row">
          <button type="button" className="btn btn-primary" disabled={busy !== null} onClick={() => void save()}>
            {busy === 'save' ? t('common.saving') : t('common.save')}
          </button>
          <button type="button" className="btn" disabled={busy !== null} onClick={() => void test()}>
            {busy === 'test' ? t('common.sending') : t('heartbeat.test')}
          </button>
        </div>
      </div>
    </Card>
  );
}
