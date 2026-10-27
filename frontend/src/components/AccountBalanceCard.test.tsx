import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import AccountBalanceCard from './AccountBalanceCard';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const SNAPSHOT = {
  exchange: 'binance',
  environment: 'testnet',
  testnet: true,
  cash_usdt: 1000,
  free_usdt: 900,
  locked_usdt: 100,
  equity_usdt: 6000,
  balances: [
    { asset: 'BTC', total: 0.1, value_usdt: 5000 },
    { asset: 'USDT', total: 1000, value_usdt: 1000 },
  ],
  unpriced_assets: [],
  realized_pnl: 5,
  unrealized_pnl: -2,
  total_pnl: 3,
  baseline_equity: 5400,
  baseline_at: '2026-10-20T10:00:00Z',
  change_usdt: 600,
  change_pct: 11.11,
  as_of: '2026-10-26T10:00:00Z',
};

describe('AccountBalanceCard', () => {
  it('shows equity and the change since the reference', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(SNAPSHOT), { status: 200 }));
    render(<AccountBalanceCard />);

    // es-ES does not group four-digit numbers: 6000 renders as "6000,00".
    expect(await screen.findByText(/^\$6\.?000,00$/)).toBeTruthy();
    expect(screen.getByText('+600,00 $').className).toContain('pos');
    expect(screen.getByText('Testnet')).toBeTruthy();
  });

  it('invites the user to connect when the exchange is not configured', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ detail: 'Exchange API credentials are not configured' }), { status: 400 }),
    );
    const onConnect = vi.fn();
    render(<AccountBalanceCard onConnect={onConnect} />);

    const button = await screen.findByRole('button', { name: 'Conectar Binance' });
    button.click();
    expect(onConnect).toHaveBeenCalledOnce();
  });
});
