import { useEffect, useState } from 'react';
import type { ModelOption, ProviderId } from '../hooks/useIntegrations';
import { CATALOG_PROVIDERS, type ModelCatalog, OPENROUTER_FREE_ROUTER } from '../hooks/useModelCatalog';
import { useI18n } from '../i18n/core';

const CUSTOM = '__custom__';

/**
 * Model dropdown fed by the provider's live catalogue (free models first),
 * with "Other…" for a model the list does not show. Providers without a
 * catalogue fall back to a text field.
 */
export function ModelPicker({
  provider,
  configured,
  value,
  onChange,
  catalog,
  defaultLabel,
}: {
  provider: ProviderId;
  configured: boolean;
  value: string;
  onChange: (model: string) => void;
  catalog: ModelCatalog;
  /** When set, an empty value is offered as "provider default" with this label. */
  defaultLabel?: string;
}) {
  const { t } = useI18n();
  const [freeOnly, setFreeOnly] = useState(true);
  const [typing, setTyping] = useState(false);
  const { options, loading, error } = catalog.entry(provider);

  const { ensure } = catalog;
  useEffect(() => ensure(provider, configured), [ensure, provider, configured]);

  if (!options) {
    return (
      <div className="stack">
        <input
          className="input mono"
          placeholder={defaultLabel ?? t('llm.order.modelPlaceholder')}
          value={value}
          onChange={(event) => onChange(event.target.value)}
        />
        <span className="muted xsmall">
          {loading
            ? t('llm.order.loading')
            : error
              ? t('llm.order.loadFailed', { detail: error })
              : CATALOG_PROVIDERS.has(provider) && !configured
                ? t('llm.order.needsKey')
                : null}
        </span>
      </div>
    );
  }

  const shown = options.filter((option) => !freeOnly || option.free);
  const known = value === '' ? defaultLabel !== undefined : options.some((option) => option.id === value);
  const custom = typing || !known;
  const label = (option: ModelOption) =>
    option.id === OPENROUTER_FREE_ROUTER
      ? t('llm.order.freeRouter')
      : option.free
        ? `${option.id} · ${t('llm.order.free')}`
        : option.id;

  return (
    <div className="stack">
      <select
        className="select mono"
        value={custom ? CUSTOM : value}
        onChange={(event) => {
          if (event.target.value === CUSTOM) {
            setTyping(true);
            return;
          }
          setTyping(false);
          onChange(event.target.value);
        }}
      >
        {defaultLabel !== undefined && <option value="">{defaultLabel}</option>}
        {/* Keep the current choice selectable even when "free only" hides it. */}
        {known && value && !shown.some((option) => option.id === value) && (
          <option value={value}>{value}</option>
        )}
        {shown.map((option) => (
          <option key={option.id} value={option.id}>
            {label(option)}
          </option>
        ))}
        <option value={CUSTOM}>{t('llm.order.custom')}</option>
      </select>
      {custom && (
        <input
          className="input mono"
          placeholder={t('llm.order.customPlaceholder')}
          value={value}
          onChange={(event) => onChange(event.target.value)}
        />
      )}
      <label className="check small">
        <input type="checkbox" checked={freeOnly} onChange={(event) => setFreeOnly(event.target.checked)} />
        <span>
          {t('llm.order.freeOnly')} ·{' '}
          {t('llm.order.loaded', { free: options.filter((option) => option.free).length, total: options.length })}
        </span>
      </label>
    </div>
  );
}
