import { createContext, useContext } from 'react';
import { en } from './en';
import { es, type MessageKey } from './es';

export type Lang = 'es' | 'en';
export type { MessageKey };
export type Params = Record<string, string | number | boolean | null | undefined>;
export type Translate = (key: MessageKey, params?: Params) => string;

const CATALOGS: Record<Lang, Record<MessageKey, string>> = { es, en };
export const STORAGE_KEY = 'ata.lang';
export const LANG_LABEL: Record<Lang, string> = { es: 'Español', en: 'English' };

export function readStoredLang(): Lang | null {
  try {
    const value = window.localStorage.getItem(STORAGE_KEY);
    return value === 'es' || value === 'en' ? value : null;
  } catch {
    return null;
  }
}

/** Browser language first (navigator.languages), Spanish for any es-* region. */
export function detectLang(): Lang {
  const stored = readStoredLang();
  if (stored) return stored;
  const candidates = typeof navigator === 'undefined' ? [] : [...(navigator.languages ?? []), navigator.language];
  for (const candidate of candidates) {
    const base = (candidate ?? '').toLowerCase().split('-')[0];
    if (base === 'es') return 'es';
    if (base === 'en') return 'en';
  }
  return 'en';
}

function interpolate(template: string, params?: Params): string {
  if (!params) return template;
  return template.replace(/\{(\w+)\}/g, (match, name: string) => {
    const value = params[name];
    return value === undefined || value === null ? match : String(value);
  });
}

export function translator(lang: Lang): Translate {
  const catalog = CATALOGS[lang];
  return (key, params) => interpolate(catalog[key] ?? es[key] ?? key, params);
}

/** Look up a key built at runtime (e.g. from a backend log key). */
export function hasKey(key: string): key is MessageKey {
  return key in es;
}

export interface I18nValue {
  lang: Lang;
  setLang: (lang: Lang) => void;
  t: Translate;
}

// Spanish by default so components render without a provider (tests).
export const I18nContext = createContext<I18nValue>({ lang: 'es', setLang: () => undefined, t: translator('es') });

export function useI18n(): I18nValue {
  return useContext(I18nContext);
}
