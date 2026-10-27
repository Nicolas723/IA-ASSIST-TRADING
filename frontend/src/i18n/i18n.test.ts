import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { setFormatLocale } from '../lib/format';
import { en } from './en';
import { es } from './es';
import { detectLang, translator } from './core';
import { logText } from './labels';

// Recent Node versions leave jsdom without localStorage; use a memory stub.
beforeEach(() => {
  const store = new Map<string, string>();
  vi.stubGlobal('localStorage', {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => store.set(key, value),
    removeItem: (key: string) => store.delete(key),
    clear: () => store.clear(),
  });
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  setFormatLocale('es-ES');
});

describe('catalogs', () => {
  it('have the same keys and the same placeholders', () => {
    expect(Object.keys(en).sort()).toEqual(Object.keys(es).sort());
    const placeholders = (text: string) => (text.match(/\{\w+\}/g) ?? []).sort();
    for (const key of Object.keys(es) as Array<keyof typeof es>) {
      expect(placeholders(en[key]), key).toEqual(placeholders(es[key]));
    }
  });

  it('translate every message key the backend engine emits', () => {
    const engine = readFileSync(
      resolve(__dirname, '../../../backend/src/application/services/autonomous_engine.py'),
      'utf-8',
    );
    const keys = [...engine.matchAll(/key="([a-z_.]+)"/g)].map((match) => `log.${match[1]}`);
    expect(keys.length).toBeGreaterThan(30);
    const missing = keys.filter((key) => !(key in es));
    expect(missing).toEqual([]);
  });
});

describe('language detection', () => {
  it('uses Spanish for any Spanish region and English otherwise', () => {
    vi.spyOn(navigator, 'languages', 'get').mockReturnValue(['es-CO', 'en-US']);
    expect(detectLang()).toBe('es');
    vi.spyOn(navigator, 'languages', 'get').mockReturnValue(['fr-FR', 'en-GB']);
    expect(detectLang()).toBe('en');
    vi.spyOn(navigator, 'languages', 'get').mockReturnValue(['de-DE']);
    expect(detectLang()).toBe('en');
  });

  it('prefers the language the user picked', () => {
    vi.spyOn(navigator, 'languages', 'get').mockReturnValue(['es-ES']);
    window.localStorage.setItem('ata.lang', 'en');
    expect(detectLang()).toBe('en');
  });
});

describe('bot log rendering', () => {
  const entry = {
    key: 'decision.standby',
    params: { signal: 'BUY', score: 46, price: 1.3 },
    message: 'AI Committee Verdict: BUY … Standing by (No trade criteria met).',
  };

  it('renders the same entry in each language', () => {
    expect(logText(translator('es'), entry)).toBe('Comité: Compra (fuerza 46 %) a $1,30 · Sin condiciones para operar.');
    setFormatLocale('en-US');
    expect(logText(translator('en'), entry)).toBe('Committee: Buy (strength 46 %) at $1.30 · No conditions to trade.');
  });

  it('falls back to the stored text for entries without a known key', () => {
    expect(logText(translator('en'), { message: 'legacy text' })).toBe('legacy text');
    expect(logText(translator('en'), { key: 'unknown.event', message: 'raw' })).toBe('raw');
  });

  it('formats executed orders with localized side and prices', () => {
    const order = {
      key: 'order.executed',
      params: { side: 'buy', quantity: 0.0006, symbol: 'BTC/USDT', price: 80000, sl: 78400, tp: 83200 },
      message: '',
    };
    setFormatLocale('en-US');
    expect(logText(translator('en'), order)).toBe(
      'Order filled: Buy 0.0006 BTC/USDT at $80,000.00 · stop $78,400.00 · target $83,200.00',
    );
  });
});
