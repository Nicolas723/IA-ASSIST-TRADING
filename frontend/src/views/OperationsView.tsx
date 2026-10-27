import { Card, Empty, Notice, PageHead, Pill, Skeleton, Stat } from '../components/ui';
import { usePolling } from '../hooks/usePolling';
import { useI18n } from '../i18n/core';
import { modeLabel, orderStatusLabel, sideLabel } from '../i18n/labels';
import { ApiError, apiGet } from '../lib/api';
import { fmtDateTime, fmtInt, fmtNumber, fmtPrice, fmtSigned, toNumber, toneClass } from '../lib/format';
import type { OrderData, PositionData, TradeOutcomeData } from '../types';

/** Real positions need exchange keys; without them there are simply none. */
async function realPositions(): Promise<PositionData[]> {
  try {
    return await apiGet<PositionData[]>('/api/v1/trading/real/positions');
  } catch (error) {
    if (error instanceof ApiError && error.status < 500) return [];
    throw error;
  }
}

export function OperationsView() {
  const { t } = useI18n();
  // Only real activity (Testnet or Mainnet); the simulated mode was retired.
  const modeQuery = '&mode=real';

  const positions = usePolling<PositionData[]>('positions', realPositions, 15000);
  const outcomes = usePolling<TradeOutcomeData[]>(
    'outcomes',
    () => apiGet<TradeOutcomeData[]>(`/api/v1/mlops/outcomes?limit=100${modeQuery}`),
    30000,
  );
  const orders = usePolling<OrderData[]>(
    'orders',
    () => apiGet<OrderData[]>(`/api/v1/trading/orders/history?limit=200${modeQuery}`),
    30000,
  );

  const closed = (outcomes.data ?? []).filter((item) => item.status === 'closed');
  const wins = closed.filter((item) => item.outcome === 'win').length;
  const net = closed.reduce((sum, item) => sum + (toNumber(item.net_pnl) ?? 0), 0);

  return (
    <>
      <PageHead
        title={t('ops.title')}
        description={t('ops.description')}
      />

      <div className="stack-lg">
        <div className="stats">
          <Stat label={t('ops.stat.closed')} value={fmtInt(closed.length)} />
          <Stat
            label={t('ops.stat.winRate')}
            value={closed.length ? `${fmtNumber((wins / closed.length) * 100, 0)} %` : '—'}
            sub={t('ops.stat.wins', { wins, total: closed.length })}
          />
          <Stat label={t('ops.stat.net')} value={fmtSigned(net, 2, ' $')} tone={toneClass(net)} />
          <Stat label={t('ops.stat.open')} value={fmtInt(positions.data?.length ?? 0)} />
        </div>

        <Card title={t('ops.positions.title')} subtitle={t('ops.positions.subtitle')}>
          {positions.loading ? (
            <Skeleton height={60} />
          ) : positions.error ? (
            <Notice tone="error">{positions.error}</Notice>
          ) : (positions.data ?? []).length === 0 ? (
            <Empty>{t('ops.positions.empty')}</Empty>
          ) : (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>{t('ops.col.market')}</th>
                    <th>{t('ops.col.mode')}</th>
                    <th>{t('ops.col.quantity')}</th>
                    <th>{t('ops.col.entry')}</th>
                    <th>{t('ops.col.now')}</th>
                    <th>{t('ops.col.result')}</th>
                    <th>{t('ops.col.stopTarget')}</th>
                    <th>{t('ops.col.opened')}</th>
                  </tr>
                </thead>
                <tbody>
                  {(positions.data ?? []).map((position) => (
                    <tr key={position.id}>
                      <td className="strong">{position.symbol}</td>
                      <td>{modeLabel(t, position.mode)}</td>
                      <td>{fmtNumber(position.quantity, 6)}</td>
                      <td>{fmtPrice(position.entry_price)}</td>
                      <td>{fmtPrice(position.current_price)}</td>
                      <td className={toneClass(position.unrealized_pnl)}>
                        {fmtSigned(position.unrealized_pnl, 2, ' $')}{' '}
                        <span className="muted">({fmtSigned(position.unrealized_pnl_pct, 2, ' %')})</span>
                      </td>
                      <td className="muted">
                        {fmtPrice(position.stop_loss)} / {fmtPrice(position.take_profit)}
                      </td>
                      <td className="muted">{fmtDateTime(position.opened_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        <Card title={t('ops.trades.title')} subtitle={t('ops.trades.subtitle')}>
          {outcomes.loading ? (
            <Skeleton height={80} />
          ) : outcomes.error ? (
            <Notice tone="error">{outcomes.error}</Notice>
          ) : (outcomes.data ?? []).length === 0 ? (
            <Empty>{t('ops.trades.empty')}</Empty>
          ) : (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>{t('ops.col.opened')}</th>
                    <th>{t('ops.col.market')}</th>
                    <th>{t('ops.col.mode')}</th>
                    <th>{t('ops.col.entry')}</th>
                    <th>{t('ops.col.exit')}</th>
                    <th>{t('ops.col.result')}</th>
                    <th>{t('ops.col.status')}</th>
                  </tr>
                </thead>
                <tbody>
                  {(outcomes.data ?? []).map((trade) => (
                    <tr key={trade.decision_id}>
                      <td className="muted">{fmtDateTime(trade.opened_at)}</td>
                      <td className="strong">{trade.symbol}</td>
                      <td>{modeLabel(t, trade.mode)}</td>
                      <td>{fmtPrice(trade.entry_price)}</td>
                      <td>{trade.exit_price === null ? '—' : fmtPrice(trade.exit_price)}</td>
                      <td className={toneClass(trade.net_pnl)}>
                        {fmtSigned(trade.net_pnl, 2, ' $')}{' '}
                        <span className="muted">({fmtSigned(trade.net_pnl_pct, 2, ' %')})</span>
                      </td>
                      <td>
                        <Pill
                          tone={trade.outcome === 'win' ? 'pos' : trade.outcome === 'loss' ? 'neg' : 'default'}
                        >
                          {t(`ops.outcome.${trade.outcome}`)}
                        </Pill>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        <Card title={t('ops.orders.title')} subtitle={t('ops.orders.subtitle')}>
          {orders.loading ? (
            <Skeleton height={80} />
          ) : orders.error ? (
            <Notice tone="error">{orders.error}</Notice>
          ) : (orders.data ?? []).length === 0 ? (
            <Empty>{t('ops.orders.empty')}</Empty>
          ) : (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>{t('ops.col.date')}</th>
                    <th>{t('ops.col.market')}</th>
                    <th>{t('ops.col.side')}</th>
                    <th>{t('ops.col.quantity')}</th>
                    <th>{t('ops.col.price')}</th>
                    <th>{t('ops.col.mode')}</th>
                    <th>{t('ops.col.status')}</th>
                    <th>{t('ops.col.origin')}</th>
                  </tr>
                </thead>
                <tbody>
                  {(orders.data ?? []).map((order) => (
                    <tr key={order.id}>
                      <td className="muted">{fmtDateTime(order.created_at)}</td>
                      <td className="strong">{order.symbol}</td>
                      <td>{sideLabel(t, order.side)}</td>
                      <td>{fmtNumber(order.filled_quantity || order.quantity, 6)}</td>
                      <td>{order.average_fill_price === null ? '—' : fmtPrice(order.average_fill_price)}</td>
                      <td>{modeLabel(t, order.mode)}</td>
                      <td title={order.rejection_reason ?? undefined}>{orderStatusLabel(t, order.status)}</td>
                      <td className="muted">
                        {order.coordinator_decision_id ? t('ops.origin.bot') : t('ops.origin.manual')}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>
    </>
  );
}
