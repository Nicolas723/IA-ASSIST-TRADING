import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import { setFormatLocale } from '../lib/format';
import { detectLang, I18nContext, STORAGE_KEY, translator, type Lang } from './core';

export function I18nProvider({ children, initial }: { children: ReactNode; initial?: Lang }) {
  const [lang, setLangState] = useState<Lang>(() => initial ?? detectLang());
  // Number and date formatting follow the interface language.
  setFormatLocale(lang === 'es' ? 'es-ES' : 'en-US');

  useEffect(() => {
    document.documentElement.lang = lang;
  }, [lang]);

  const setLang = useCallback((next: Lang) => {
    setLangState(next);
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // Private mode: the choice simply is not remembered.
    }
  }, []);

  const value = useMemo(() => ({ lang, setLang, t: translator(lang) }), [lang, setLang]);
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}
