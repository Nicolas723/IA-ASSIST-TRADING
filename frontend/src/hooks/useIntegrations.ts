import { apiGet } from '../lib/api';
import { usePolling, type PollingState } from './usePolling';

export type ProviderId = 'openai' | 'anthropic' | 'google' | 'openrouter' | 'deepseek' | 'groq' | 'ollama';

export interface ProviderView {
  provider: ProviderId;
  label: string;
  configured: boolean;
  masked_key: string;
  model: string;
  base_url: string | null;
  requires_key: boolean;
  supports_base_url: boolean;
  enabled: boolean;
}

export interface IntegrationsData {
  llm: {
    providers: ProviderView[];
    primary_provider: ProviderId;
    fallback_providers: ProviderId[];
    fallback_enabled: boolean;
    configured_providers: string[];
    scan_mode: string;
    note: string;
  };
  telegram: { configured: boolean; masked_token: string; chat_id: string; enabled: boolean };
  heartbeat: { configured: boolean; masked_url: string; interval_seconds: number; running: boolean };
}

/** UI-managed LLM, Telegram and heartbeat settings (secrets come masked). */
export function useIntegrations(): PollingState<IntegrationsData> {
  return usePolling<IntegrationsData>('integrations', () =>
    apiGet<IntegrationsData>('/api/v1/settings/integrations'),
  );
}
