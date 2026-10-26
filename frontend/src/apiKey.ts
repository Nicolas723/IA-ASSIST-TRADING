// Optional API key support. When VITE_API_KEY is set at build/dev time, every
// request to /api/* carries it as X-API-Key. The backend only enforces the
// header when API_AUTH_TOKEN is configured, so local development is unchanged.
const apiKey = (import.meta.env.VITE_API_KEY as string | undefined)?.trim();

if (apiKey) {
  const originalFetch = window.fetch.bind(window);

  window.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
    const url =
      typeof input === 'string'
        ? input
        : input instanceof URL
          ? input.toString()
          : input.url;

    const isApiRequest =
      url.startsWith('/api') || url.startsWith(`${window.location.origin}/api`);

    if (!isApiRequest) {
      return originalFetch(input, init);
    }

    const headers = new Headers(init?.headers);
    headers.set('X-API-Key', apiKey);
    return originalFetch(input, { ...init, headers });
  };
}
