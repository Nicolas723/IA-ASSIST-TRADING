import { useCallback, useRef, useState } from 'react';
import { apiGet, errorMessage } from '../lib/api';
import type { ModelOption, ProviderId } from './useIntegrations';

/** Providers whose live catalogue the backend can list. */
export const CATALOG_PROVIDERS = new Set<ProviderId>(['openrouter', 'groq', 'deepseek', 'openai']);
/** OpenRouter's public catalogue needs no key; the others use the stored one. */
const PUBLIC_CATALOG = new Set<ProviderId>(['openrouter']);
/** Rotates across OpenRouter's free models; must match the backend constant. */
export const OPENROUTER_FREE_ROUTER = 'openrouter/free';

export interface CatalogEntry {
  options?: ModelOption[];
  loading?: boolean;
  error?: string;
}

export interface ModelCatalog {
  entry: (provider: ProviderId) => CatalogEntry;
  ensure: (provider: ProviderId, configured: boolean) => void;
}

/** One catalogue cache per settings form, loaded on demand per provider. */
export function useModelCatalog(): ModelCatalog {
  const [entries, setEntries] = useState<Partial<Record<ProviderId, CatalogEntry>>>({});
  const requested = useRef(new Set<ProviderId>());

  const ensure = useCallback((provider: ProviderId, configured: boolean) => {
    if (!CATALOG_PROVIDERS.has(provider) || (!configured && !PUBLIC_CATALOG.has(provider))) return;
    if (requested.current.has(provider)) return;
    requested.current.add(provider);
    setEntries((current) => ({ ...current, [provider]: { loading: true } }));
    apiGet<ModelOption[]>(`/api/v1/settings/llm/models/${provider}`)
      .then((options) => setEntries((current) => ({ ...current, [provider]: { options } })))
      .catch((error: unknown) =>
        setEntries((current) => ({ ...current, [provider]: { error: errorMessage(error) } })),
      );
  }, []);

  return { entry: (provider) => entries[provider] ?? {}, ensure };
}
