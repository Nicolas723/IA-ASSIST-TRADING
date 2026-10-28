import { useEffect, useState } from 'react';
import toast from 'react-hot-toast';
import type { PollingState } from '../hooks/usePolling';
import { useI18n } from '../i18n/core';
import { apiGet, apiSend, errorMessage } from '../lib/api';
import { fmtSigned, formatLocale, toneClass } from '../lib/format';
import type { RuntimeTradingConfigData } from '../types';
import { Card, Notice, Pill, Skeleton } from './ui';

/** Must match MAX_SCAN_SYMBOLS in the backend settings. */
const MAX_MARKETS = 25;

interface MarketOption {
  symbol: string;
  volume_usd_24h: number;
  change_pct_24h: number;
  tradable: boolean;
}

function compactUsd(value: number): string {
  return new Intl.NumberFormat(formatLocale(), {
    style: 'currency',
    currency: 'USD',
    notation: 'compact',
    maximumFractionDigits: 1,
  }).format(value);
}

function normalize(raw: string): string {
  const symbol = raw.trim().toUpperCase().replace(/[-_]/g, '/');
  return symbol && !symbol.includes('/') ? `${symbol}/USDT` : symbol;
}

export function MarketsSettings({ runtime }: { runtime: PollingState<RuntimeTradingConfigData> }) {
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
  return <MarketsForm key={JSON.stringify(runtime.data.shadow_scan_symbols)} config={runtime.data} onSaved={runtime.setData} />;
}

function MarketsForm({
  config,
  onSaved,
}: {
  config: RuntimeTradingConfigData;
  onSaved: (value: RuntimeTradingConfigData) => void;
}) {
  const { t } = useI18n();
  const [selected, setSelected] = useState<string[]>(config.shadow_scan_symbols);
  const [custom, setCustom] = useState('');
  const [catalog, setCatalog] = useState<MarketOption[] | null>(null);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let alive = true;
    apiGet<MarketOption[]>('/api/v1/market/catalog')
      .then((options) => alive && setCatalog(options))
      .catch((error: unknown) => alive && setCatalogError(errorMessage(error)));
    return () => {
      alive = false;
    };
  }, []);

  const full = selected.length >= MAX_MARKETS;
  const toggle = (symbol: string) =>
    setSelected((current) =>
      current.includes(symbol)
        ? current.filter((item) => item !== symbol)
        : current.length >= MAX_MARKETS
          ? current
          : [...current, symbol],
    );
  const addCustom = () => {
    const symbol = normalize(custom);
    if (symbol && !selected.includes(symbol) && !full) setSelected([...selected, symbol]);
    setCustom('');
  };

  const changed = selected.join(',') !== config.shadow_scan_symbols.join(',');
  const save = async () => {
    if (selected.length === 0) {
      toast.error(t('markets.toast.empty'));
      return;
    }
    setBusy(true);
    try {
      // The scanned markets are also the ones allowed to trade, so a market
      // picked here can always be executed (and nothing outside it can).
      const snapshot = await apiSend<RuntimeTradingConfigData>('PUT', '/api/v1/trading/runtime-config', {
        shadow_scan_symbols: selected,
        real_trading_allowed_symbols: selected,
      });
      onSaved(snapshot);
      toast.success(t('markets.toast.saved', { count: selected.length }));
    } catch (error) {
      toast.error(t('common.saveFailed', { detail: errorMessage(error) }));
    } finally {
      setBusy(false);
    }
  };

  const untradable = new Set((catalog ?? []).filter((option) => !option.tradable).map((option) => option.symbol));

  return (
    <div className="stack-lg">
      <Card
        title={t('markets.title')}
        subtitle={t('markets.subtitle', { max: MAX_MARKETS })}
        actions={<Pill tone={full ? 'solid' : 'default'}>{t('markets.count', { count: selected.length, max: MAX_MARKETS })}</Pill>}
      >
        <div className="stack">
          {selected.length === 0 ? (
            <Notice tone="warn">{t('markets.empty')}</Notice>
          ) : (
            <div className="market-chips">
              {selected.map((symbol) => (
                <button
                  key={symbol}
                  type="button"
                  className={`market-chip is-on${untradable.has(symbol) ? ' is-warn' : ''}`}
                  title={t('markets.remove', { symbol })}
                  onClick={() => toggle(symbol)}
                >
                  {symbol} <span aria-hidden="true">✕</span>
                </button>
              ))}
            </div>
          )}
          <div className="row">
            <input
              className="input mono"
              style={{ maxWidth: 220 }}
              placeholder={t('markets.custom.placeholder')}
              value={custom}
              onChange={(event) => setCustom(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') addCustom();
              }}
            />
            <button type="button" className="btn btn-sm" disabled={full || !custom.trim()} onClick={addCustom}>
              {t('markets.custom.add')}
            </button>
          </div>
          {selected.length > 5 && <p className="muted xsmall">{t('markets.scanModeHint')}</p>}
          <div className="row">
            <button type="button" className="btn btn-primary" disabled={busy || !changed} onClick={() => void save()}>
              {busy ? t('common.saving') : t('markets.save')}
            </button>
            <span className="muted xsmall">{t('markets.liveNote')}</span>
          </div>
        </div>
      </Card>

      <Card title={t('markets.suggested')} subtitle={t('markets.suggested.subtitle')}>
        {catalogError ? (
          <Notice tone="error">{t('markets.catalogFailed', { detail: catalogError })}</Notice>
        ) : !catalog ? (
          <Skeleton height={120} />
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>{t('markets.col.market')}</th>
                  <th className="num">{t('markets.col.volume')}</th>
                  <th className="num">{t('markets.col.change')}</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {catalog.map((option) => {
                  const on = selected.includes(option.symbol);
                  return (
                    <tr key={option.symbol}>
                      <td className="mono">{option.symbol}</td>
                      <td className="num">{compactUsd(option.volume_usd_24h)}</td>
                      <td className={`num ${toneClass(option.change_pct_24h)}`}>
                        {fmtSigned(option.change_pct_24h, 2, ' %')}
                      </td>
                      <td className="num">
                        {option.tradable ? (
                          <button
                            type="button"
                            className={`btn btn-sm${on ? '' : ' btn-ghost'}`}
                            disabled={!on && full}
                            onClick={() => toggle(option.symbol)}
                          >
                            {on ? t('markets.added') : t('markets.add')}
                          </button>
                        ) : (
                          <Pill>{t('markets.notOnNetwork')}</Pill>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
