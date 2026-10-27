import { StrictMode, Component, type ErrorInfo, type ReactNode } from 'react';
import { createRoot } from 'react-dom/client';
import './index.css';
import './apiKey';
import App from './App.tsx';
import { detectLang, translator } from './i18n/core';
import { I18nProvider } from './i18n/provider';

interface ErrorBoundaryState {
  error: Error | null;
}

class ErrorBoundary extends Component<{ children: ReactNode }, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('App crash:', error, info.componentStack);
  }

  render() {
    if (!this.state.error) return this.props.children;
    // Rendered outside the provider on purpose: it must work even if i18n broke.
    const t = translator(detectLang());
    return (
      <main className="main" style={{ maxWidth: 720 }}>
        <div className="card stack">
          <h1 style={{ fontSize: 22 }}>{t('crash.title')}</h1>
          <p className="muted">{t('crash.body')}</p>
          <pre className="mono xsmall" style={{ whiteSpace: 'pre-wrap', margin: 0 }}>
            {this.state.error.toString()}
          </pre>
          <div className="row">
            <button type="button" className="btn btn-primary" onClick={() => window.location.reload()}>
              {t('crash.reload')}
            </button>
            <button type="button" className="btn" onClick={() => this.setState({ error: null })}>
              {t('crash.retry')}
            </button>
          </div>
        </div>
      </main>
    );
  }
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary>
      <I18nProvider>
        <App />
      </I18nProvider>
    </ErrorBoundary>
  </StrictMode>,
);
