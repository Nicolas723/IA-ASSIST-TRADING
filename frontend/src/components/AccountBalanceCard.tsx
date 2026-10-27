import { useState } from 'react';
import toast from 'react-hot-toast';
import { usePolling } from '../hooks/usePolling';
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
      toast.success('Referencia fijada en el saldo actual.');
    } catch (error) {
      toast.error(`No se pudo fijar: ${errorMessage(error)}`);
    } finally {
      setResetting(false);
    }
  };

  const data = snapshot.data;

  if (snapshot.loading) {
    return (
      <Card title="Saldo de la cuenta" icon="wallet">
        <Skeleton height={88} />
      </Card>
    );
  }

  if (!data) {
    return (
      <Card
        title="Saldo de la cuenta"
        icon="wallet"
        subtitle="Conecta tus claves de Binance para ver aquí tu saldo real o de Testnet."
        actions={
          <>
            <button type="button" className="btn btn-sm" onClick={() => void snapshot.refresh()}>
              Reintentar
            </button>
            {onConnect && (
              <button type="button" className="btn btn-sm btn-primary" onClick={onConnect}>
                Conectar Binance
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
      title="Saldo de la cuenta"
      icon="wallet"
      subtitle={`${data.exchange} · actualizado ${fmtDateTime(data.as_of)}`}
      actions={
        <>
          <Pill tone={data.testnet ? 'default' : 'neg'}>{data.testnet ? 'Testnet' : 'Dinero real'}</Pill>
          <button
            type="button"
            className="btn btn-sm"
            disabled={resetting}
            title="Usa el saldo actual como punto de partida para medir ganancias"
            onClick={() => void resetBaseline()}
          >
            {resetting ? 'Guardando…' : 'Fijar referencia'}
          </button>
        </>
      }
    >
      <div className="stack">
        <div className="stats">
          <Stat label="Patrimonio total" value={fmtUsd(data.equity_usdt)} />
          <Stat
            label="Efectivo USDT"
            value={fmtUsd(data.cash_usdt)}
            sub={`Libre ${fmtUsd(data.free_usdt)} · en órdenes ${fmtUsd(data.locked_usdt)}`}
          />
          <Stat
            label="Desde la referencia"
            value={fmtSigned(data.change_usdt, 2, ' $')}
            tone={toneClass(data.change_usdt)}
            sub={data.change_pct === null ? 'Sin referencia' : fmtSigned(data.change_pct, 2, ' %')}
          />
          <Stat
            label="Resultado del asistente"
            value={fmtSigned(data.total_pnl, 2, ' $')}
            tone={toneClass(data.total_pnl)}
            sub={`Cerrado ${fmtSigned(data.realized_pnl)} · abierto ${fmtSigned(data.unrealized_pnl)}`}
          />
        </div>

        {assets.length > 0 && (
          <div className="row">
            {assets.map((asset) => (
              <Pill key={asset.asset}>
                <span className="num">
                  {asset.asset} {fmtNumber(asset.total, asset.total >= 1 ? 2 : 6)}
                  {asset.value_usdt === null ? ' · sin precio' : ` · ${fmtUsd(asset.value_usdt)}`}
                </span>
              </Pill>
            ))}
          </div>
        )}

        <p className="muted xsmall">
          Referencia: {fmtUsd(data.baseline_equity)} desde {fmtDateTime(data.baseline_at)}. Los depósitos y retiros
          también cambian esta cifra; fija una nueva referencia después de moverlos.
          {data.unpriced_assets.length > 0 && ` Sin precio público: ${data.unpriced_assets.join(', ')}.`}
        </p>

        {snapshot.error && <Notice tone="warn">La última actualización falló: {snapshot.error}</Notice>}
      </div>
    </Card>
  );
}
