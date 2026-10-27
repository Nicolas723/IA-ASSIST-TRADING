import { useCallback, useEffect, useState } from 'react';
import { Toaster } from 'react-hot-toast';
import { Icon, type IconName } from './components/Icon';
import { Pill } from './components/ui';
import { usePolling } from './hooks/usePolling';
import { LANG_LABEL, useI18n, type Lang, type MessageKey } from './i18n/core';
import { apiGet } from './lib/api';
import type { AutonomousBotStatus, RuntimeTradingConfigData } from './types';
import { AdvancedView } from './views/AdvancedView';
import { AssistantView } from './views/AssistantView';
import { OperationsView } from './views/OperationsView';
import { SettingsView } from './views/SettingsView';

export type Page = 'assistant' | 'operations' | 'settings' | 'advanced';

const PAGES: ReadonlyArray<{ id: Page; hash: string; label: MessageKey; icon: IconName }> = [
  { id: 'assistant', hash: '#/asistente', label: 'nav.assistant', icon: 'activity' },
  { id: 'operations', hash: '#/operaciones', label: 'nav.operations', icon: 'list' },
  { id: 'settings', hash: '#/configuracion', label: 'nav.settings', icon: 'settings' },
  { id: 'advanced', hash: '#/avanzado', label: 'nav.advanced', icon: 'layers' },
];

function pageFromHash(hash: string): Page {
  return PAGES.find((page) => hash.startsWith(page.hash))?.id ?? 'assistant';
}

/** Section after the page, e.g. "#/configuracion/ia" → "ia". */
function sectionFromHash(hash: string): string | null {
  const parts = hash.replace(/^#\//, '').split('/');
  return parts[1] || null;
}

export default function App() {
  const { t, lang, setLang } = useI18n();
  const [hash, setHash] = useState(() => window.location.hash);
  const page = pageFromHash(hash);
  const section = sectionFromHash(hash);

  useEffect(() => {
    const onHash = () => setHash(window.location.hash);
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);

  useEffect(() => {
    document.title = `${t(PAGES.find((item) => item.id === page)?.label ?? 'nav.assistant')} · Trading Assistant`;
  }, [page, t]);

  const navigate = useCallback((target: Page, sub?: string) => {
    const base = PAGES.find((item) => item.id === target)?.hash ?? '#/asistente';
    window.location.hash = sub ? `${base}/${sub}` : base;
    window.scrollTo({ top: 0 });
  }, []);

  const [symbol, setSymbol] = useState('BTC/USDT');
  const [timeframe, setTimeframe] = useState('1h');

  const bot = usePolling<AutonomousBotStatus>(
    'bot',
    () => apiGet<AutonomousBotStatus>('/api/v1/autonomous/status'),
    3000,
  );
  const runtime = usePolling<RuntimeTradingConfigData>(
    'runtime',
    () => apiGet<RuntimeTradingConfigData>('/api/v1/trading/runtime-config'),
    30000,
  );

  const mainnet = runtime.data?.trading_environment === 'mainnet';
  const running = Boolean(bot.data?.is_running);

  return (
    <div className="app">
      <Toaster position="bottom-right" toastOptions={{ className: 'toast', duration: 4000 }} />

      <header className="app-header">
        <div className="app-header-inner">
          <div className="brand">
            <span className="brand-mark">
              <Icon name="logo" size={16} />
            </span>
            <span className="brand-name">Trading Assistant</span>
          </div>

          <nav className="nav" aria-label={t('nav.label')}>
            {PAGES.map((item) => (
              <button
                key={item.id}
                type="button"
                className="nav-item"
                aria-current={page === item.id ? 'page' : undefined}
                aria-label={t(item.label)}
                onClick={() => navigate(item.id)}
              >
                <Icon name={item.icon} size={16} />
                <span className="nav-label-long" aria-hidden="true">
                  {t(item.label)}
                </span>
              </button>
            ))}
          </nav>

          <div className="header-status">
            <Pill dot live={running} tone={running ? 'solid' : 'default'} title={t('header.botState')}>
              {running ? t('header.running') : t('header.paused')}
            </Pill>
            <Pill tone={mainnet ? 'neg' : 'default'} title={t('header.venue')}>
              {mainnet ? t('venue.mainnet') : t('venue.testnet')}
            </Pill>
            <label className="sr-only" htmlFor="lang">
              {t('header.language')}
            </label>
            <select
              id="lang"
              className="select lang-select"
              value={lang}
              onChange={(event) => setLang(event.target.value as Lang)}
            >
              {(Object.keys(LANG_LABEL) as Lang[]).map((code) => (
                <option key={code} value={code}>
                  {code.toUpperCase()}
                </option>
              ))}
            </select>
          </div>
        </div>
      </header>

      <main className="main">
        {page === 'assistant' && (
          <AssistantView
            symbol={symbol}
            timeframe={timeframe}
            onSymbolChange={setSymbol}
            onTimeframeChange={setTimeframe}
            bot={bot}
            runtime={runtime}
            navigate={navigate}
          />
        )}
        {page === 'operations' && <OperationsView />}
        {page === 'settings' && <SettingsView section={section} navigate={navigate} runtime={runtime} />}
        {page === 'advanced' && (
          <AdvancedView section={section} navigate={navigate} symbol={symbol} timeframe={timeframe} />
        )}
      </main>
    </div>
  );
}
