import { useCallback, useEffect, useRef, useState } from 'react';
import { errorMessage } from '../lib/api';

export interface PollingState<T> {
  data: T | null;
  error: string | null;
  /** True until the first response (or error) arrives. */
  loading: boolean;
  /** Fetch now; resolves once the state is updated. */
  refresh: () => Promise<void>;
  setData: (value: T | null) => void;
}

/**
 * Load data on mount and whenever `key` changes, then poll every `intervalMs`.
 *
 * Polling pauses while the browser tab is hidden, so a forgotten tab does not
 * keep hitting the exchange. Pass `key = null` to stay idle.
 */
export function usePolling<T>(
  key: string | null,
  fetcher: () => Promise<T>,
  intervalMs: number | null = null,
): PollingState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [settledKey, setSettledKey] = useState<string | null>(null);
  const fetcherRef = useRef(fetcher);
  const requestRef = useRef(0);

  useEffect(() => {
    fetcherRef.current = fetcher;
  });

  const run = useCallback(async (forKey: string | null) => {
    const request = ++requestRef.current;
    try {
      const value = await fetcherRef.current();
      // A slower, older request must not overwrite a newer answer.
      if (request !== requestRef.current) return;
      setData(value);
      setError(null);
    } catch (err) {
      if (request !== requestRef.current) return;
      setError(errorMessage(err));
    } finally {
      if (request === requestRef.current) setSettledKey(forKey);
    }
  }, []);

  useEffect(() => {
    if (key === null) return;
    void run(key);
    if (!intervalMs) return;
    const timer = window.setInterval(() => {
      if (!document.hidden) void run(key);
    }, intervalMs);
    return () => window.clearInterval(timer);
  }, [key, intervalMs, run]);

  const refresh = useCallback(() => run(key), [key, run]);

  return { data, error, loading: key !== null && settledKey !== key && data === null, refresh, setData };
}
