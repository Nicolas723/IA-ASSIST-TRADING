import { useState } from 'react';
import toast from 'react-hot-toast';
import type { IntegrationsData, ProviderId } from '../hooks/useIntegrations';
import type { PollingState } from '../hooks/usePolling';
import { useI18n } from '../i18n/core';
import { apiSend, errorMessage } from '../lib/api';
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
  const [primary, setPrimary] = useState<ProviderId>(data.llm.primary_provider);
  const [fallbacks, setFallbacks] = useState<ProviderId[]>(data.llm.fallback_providers);
  const [fallbackEnabled, setFallbackEnabled] = useState(data.llm.fallback_enabled);
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
        primary_provider: primary,
        fallback_providers: fallbacks.filter((item) => item !== primary),
        fallback_enabled: fallbackEnabled,
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

  const configured = providers.filter((provider) => provider.configured);

  return (
    <div className="stack-lg">
      <Card
        title={t('llm.chain.title')}
        subtitle={t('llm.chain.subtitle')}
        actions={
          <Pill tone={configured.length ? 'solid' : 'default'}>
            {configured.length ? t('llm.chain.active', { count: configured.length }) : t('llm.chain.none')}
          </Pill>
        }
      >
        <div className="form-grid">
          <label className="field">
            <span className="field-label">{t('llm.primary')}</span>
            <select className="select" value={primary} onChange={(event) => setPrimary(event.target.value as ProviderId)}>
              {providers.map((provider) => (
                <option key={provider.provider} value={provider.provider}>
                  {provider.label}
                  {provider.configured ? '' : ` (${t('llm.noKey')})`}
                </option>
              ))}
            </select>
          </label>
          <div className="field">
            <span className="field-label">{t('llm.fallback')}</span>
            <label className="check">
              <input
                type="checkbox"
                checked={fallbackEnabled}
                onChange={(event) => setFallbackEnabled(event.target.checked)}
              />
              <span>{t('llm.fallback.use')}</span>
            </label>
          </div>
        </div>
        <p className="muted xsmall" style={{ marginTop: 12 }}>
          {t('llm.note')}
        </p>
      </Card>

      <div className="cols">
        {providers.map((provider) => {
          const draft = drafts[provider.provider];
          const result = tests[provider.provider];
          const isFallback = fallbacks.includes(provider.provider);
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
                    {fallbackEnabled && provider.provider !== primary && (
                      <label className="check small">
                        <input
                          type="checkbox"
                          checked={isFallback}
                          onChange={(event) =>
                            setFallbacks((current) =>
                              event.target.checked
                                ? [...current.filter((item) => item !== provider.provider), provider.provider]
                                : current.filter((item) => item !== provider.provider),
                            )
                          }
                        />
                        <span>{t('llm.fallback.short')}</span>
                      </label>
                    )}
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
