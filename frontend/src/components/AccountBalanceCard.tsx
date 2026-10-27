import { useState } from 'react';
import toast from 'react-hot-toast';
import { usePolling } from '../hooks/usePolling';
import { useI18n } from '../i18n/core';
import { apiGet, apiSend, errorMessage } from '../lib/api';
import { fmtDateTime, fmtNumber, fmtSigned, fmtUsd, toneClass } from '../lib/format';
import { Card, Notice, Pill, Skeleton, Stat } from './ui';

export interface AccountSnapshotData {
  exchange: string;
  environment: string;
  testnet: boolean;
  cash_usdt: number;
  free_usdt: number;
  locked_usdt: number;
  equity_usdt: number;
  balances: Array<{ asset: string; total: number; value_usdt: number | null }>;
  unpriced_assets: string[];
  realized_pnl: number;
  unrealized_pnl: number;
  total_pnl: number;
  baseline_equity: number | null;
  baseline_at: string | null;
  change_usdt: number | null;
  change_pct: number | null;
  as_of: string;
}

const REFRESH_MS = 20_000;

export default function AccountBalanceCard({ onConnect }: { onConnect?: () => void }) {
  const { t } = useI18n();
  const snapshot = usePolling<AccountSnapshotData>(
    'account-snapshot',
    () => apiGet<AccountSnapshotData>('/api/v1/trading/real/account/snapshot'),
    REFRESH_MS,
  );
  const [resetting, setResetting] = useState(false);

  const resetBaseline = async () => {
    setResetting(true);
    try {
      snapshot.setData(await apiSend<AccountSnapshotData>('POST', '/api/v1/trading/real/account/baseline', {}));
      toast.success(t('account.toast.baseline'));
    } catch (error) {
      toast.error(t('common.saveFailed', { detail: errorMessage(error) }));
    } finally {
      setResetting(false);
    }
  };

  const data = snapshot.data;

  if (snapshot.loading) {
    return (
      <Card title={t('account.title')} icon="wallet">
        <Skeleton height={88} />
      </Card>
    );
  }

  if (!data) {
    return (
      <Card
        title={t('account.title')}
        icon="wallet"
        subtitle={t('account.connectHint')}
        actions={
          <>
            <button type="button" className="btn btn-sm" onClick={() => void snapshot.refresh()}>
              {t('common.retry')}
            </button>
            {onConnect && (
              <button type="button" className="btn btn-sm btn-primary" onClick={onConnect}>
                {t('account.connect')}
              </button>
            )}
          </>
        }
      />
    );
  }

  const assets = data.balances.filter((item) => item.total > 0);

  return (
    <Card
      title={t('account.title')}
      icon="wallet"
      subtitle={t('account.updated', { exchange: data.exchange, time: fmtDateTime(data.as_of) })}
      actions={
        <>
          <Pill tone={data.testnet ? 'default' : 'neg'}>{data.testnet ? 'Testnet' : t('venue.mainnet')}</Pill>
          <button
            type="button"
            className="btn btn-sm"
            disabled={resetting}
            title={t('account.baseline.hint')}
            onClick={() => void resetBaseline()}
          >
            {resetting ? t('common.saving') : t('account.baseline')}
          </button>
        </>
      }
    >
      <div className="stack">
        <div className="stats">
          <Stat label={t('account.equity')} value={fmtUsd(data.equity_usdt)} />
          <Stat
            label={t('account.cash')}
            value={fmtUsd(data.cash_usdt)}
            sub={t('account.cash.sub', { free: fmtUsd(data.free_usdt), locked: fmtUsd(data.locked_usdt) })}
          />
          <Stat
            label={t('account.change')}
            value={fmtSigned(data.change_usdt, 2, ' $')}
            tone={toneClass(data.change_usdt)}
            sub={data.change_pct === null ? t('account.noBaseline') : fmtSigned(data.change_pct, 2, ' %')}
          />
          <Stat
            label={t('account.botResult')}
            value={fmtSigned(data.total_pnl, 2, ' $')}
            tone={toneClass(data.total_pnl)}
            sub={t('account.botResult.sub', {
              realized: fmtSigned(data.realized_pnl),
              unrealized: fmtSigned(data.unrealized_pnl),
            })}
          />
        </div>

        {assets.length > 0 && (
          <div className="row">
            {assets.map((asset) => (
              <Pill key={asset.asset}>
                <span className="num">
                  {asset.asset} {fmtNumber(asset.total, asset.total >= 1 ? 2 : 6)}
                  {asset.value_usdt === null ? ` · ${t('account.noPrice')}` : ` · ${fmtUsd(asset.value_usdt)}`}
                </span>
              </Pill>
            ))}
          </div>
        )}

        <p className="muted xsmall">
          {t('account.baselineNote', { value: fmtUsd(data.baseline_equity), time: fmtDateTime(data.baseline_at) })}
          {data.unpriced_assets.length > 0 && ` ${t('account.unpriced', { list: data.unpriced_assets.join(', ') })}`}
        </p>

        {snapshot.error && <Notice tone="warn">{t('account.lastFailed', { detail: snapshot.error })}</Notice>}
      </div>
    </Card>
  );
}
