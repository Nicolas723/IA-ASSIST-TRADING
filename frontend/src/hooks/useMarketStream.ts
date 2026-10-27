import { useEffect, useState } from 'react';
import { apiGet } from '../lib/api';
import type { CandleItem } from '../types';

export interface MarketStream {
  candles: CandleItem[];
  livePrice: number | null;
  connected: boolean;
  loading: boolean;
}

/** Closed candles over REST plus the live price over the market WebSocket. */
export function useMarketStream(symbol: string, timeframe: string): MarketStream {
  const [candles, setCandles] = useState<CandleItem[]>([]);
  const [livePrice, setLivePrice] = useState<number | null>(null);
  const [connected, setConnected] = useState(false);
  const [loadedFor, setLoadedFor] = useState<string | null>(null);
  const requestKey = `${symbol}|${timeframe}`;

  useEffect(() => {
    let cancelled = false;
    const encoded = encodeURIComponent(symbol);
    apiGet<{ data?: CandleItem[] }>(`/api/v1/market/${encoded}/candles?timeframe=${timeframe}&limit=60`)
      .then((payload) => {
        if (cancelled) return;
        const list = payload.data ?? [];
        setCandles(list);
        if (list.length > 0) setLivePrice(list[list.length - 1].close);
      })
      .catch(() => {
        if (!cancelled) setCandles([]);
      })
      .finally(() => {
        if (!cancelled) setLoadedFor(`${symbol}|${timeframe}`);
      });
    return () => {
      cancelled = true;
    };
  }, [symbol, timeframe]);

  useEffect(() => {
    let socket: WebSocket | null = null;
    let ping: number | undefined;
    let reconnect: number | undefined;
    let attempts = 0;
    let closed = false;

    const connect = () => {
      if (closed) return;
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const url = `${protocol}//${window.location.host}/api/v1/ws/market/${symbol.replace('/', '-')}`;
      try {
        socket = new WebSocket(url);
      } catch {
        return;
      }
      socket.onopen = () => {
        attempts = 0;
        setConnected(true);
        ping = window.setInterval(() => {
          if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ action: 'ping' }));
        }, 15000);
      };
      socket.onmessage = (event) => {
        try {
          const message = JSON.parse(event.data as string);
          if (message.type !== 'price_tick' || typeof message.price !== 'number') return;
          const price: number = message.price;
          setLivePrice(price);
          setCandles((previous) => {
            if (previous.length === 0) return previous;
            const last = previous[previous.length - 1];
            return [
              ...previous.slice(0, -1),
              { ...last, close: price, high: Math.max(last.high, price), low: Math.min(last.low, price) },
            ];
          });
        } catch {
          // Ignore non-JSON frames.
        }
      };
      socket.onerror = () => setConnected(false);
      socket.onclose = () => {
        setConnected(false);
        window.clearInterval(ping);
        if (closed) return;
        const delay = Math.min(1000 * 1.5 ** attempts, 10000);
        attempts += 1;
        reconnect = window.setTimeout(connect, delay);
      };
    };

    connect();
    return () => {
      closed = true;
      window.clearInterval(ping);
      window.clearTimeout(reconnect);
      socket?.close();
    };
  }, [symbol]);

  return { candles, livePrice, connected, loading: loadedFor !== requestKey };
}
