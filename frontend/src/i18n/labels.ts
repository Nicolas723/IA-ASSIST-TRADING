// Translated labels for backend enums and the bot's activity log.

import { fmtDuration, fmtNumber, fmtPrice, fmtSigned, fmtUsd, toNumber } from '../lib/format';
import { hasKey, type MessageKey, type Params, type Translate } from './core';

function lookup(t: Translate, key: string, fallback: string): string {
  return hasKey(key) ? t(key) : fallback;
}

export const signalLabel = (t: Translate, signal: string | null | undefined): string =>
  lookup(t, `signal.${(signal ?? '').toLowerCase()}`, signal ?? '—');

export const agentLabel = (t: Translate, agent: string): string => lookup(t, `agent.${agent}`, agent);

export const modeLabel = (t: Translate, mode: string | null | undefined): string =>
  lookup(t, `mode.${(mode ?? '').toLowerCase()}`, mode ?? '—');

export const orderStatusLabel = (t: Translate, status: string): string =>
  lookup(t, `orderStatus.${status.toLowerCase()}`, status);

export const sideLabel = (t: Translate, side: string): string => lookup(t, `side.${side.toLowerCase()}`, side);

export const regimeLabel = (t: Translate, kind: 'trend' | 'volatility', value: string | null | undefined): string =>
  value ? lookup(t, `regime.${kind}.${value}`, value) : '—';

export const scanModeLabel = (t: Translate, mode: string, fallback: string): string =>
  lookup(t, `scanMode.${mode}`, fallback);

interface LogEntryLike {
  key?: string | null;
  params?: Record<string, unknown>;
  message: string;
}

/**
 * Render a bot log entry in the UI language from its key and values.
 * Entries without a known key (older data) fall back to the stored text.
 */
export function logText(t: Translate, entry: LogEntryLike): string {
  const key = entry.key ? `log.${entry.key}` : '';
  if (!hasKey(key)) return entry.message;
  const raw = entry.params ?? {};
  const params: Params = {};
  for (const [name, value] of Object.entries(raw)) {
    params[name] = typeof value === 'object' && value !== null ? JSON.stringify(value) : (value as Params[string]);
  }

  for (const name of ['price', 'sl', 'tp']) {
    if (toNumber(raw[name]) !== null) params[name] = fmtPrice(raw[name]);
  }
  if (toNumber(raw.max) !== null && entry.key !== 'decision.max_positions') params.max = fmtUsd(raw.max);
  if (toNumber(raw.return_pct) !== null) params.return_pct = fmtSigned(raw.return_pct, 2, ' %');
  if (toNumber(raw.score) !== null) params.score = fmtNumber(raw.score, entry.key === 'scan.chosen' ? 2 : 0);
  if (toNumber(raw.seconds) !== null) params.seconds = fmtDuration(raw.seconds);
  if (typeof raw.parallel === 'boolean') params.parallel = t(raw.parallel ? 'common.parallel' : 'common.sequential');
  if (typeof raw.mode === 'string' && entry.key === 'bot.mode') params.mode = modeLabel(t, raw.mode);
  if (typeof raw.side === 'string') params.side = sideLabel(t, raw.side);
  for (const name of ['allowed', 'analyzable', 'regime', 'detail']) {
    if (raw[name] === '' || raw[name] === undefined) params[name] = t('common.none');
  }
  if (typeof raw.signal === 'string') {
    params.signal = signalLabel(t, raw.signal);
    params.verdict = t('log.verdict', {
      signal: params.signal,
      score: fmtNumber(raw.score, 0),
      price: params.price ?? '—',
    });
  }
  return t(key as MessageKey, params);
}
