import { StrictMode, Component, type ErrorInfo, type ReactNode } from 'react';
import { createRoot } from 'react-dom/client';
import './index.css';
import './apiKey';
import App from './App.tsx';

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
    return (
      <main className="main" style={{ maxWidth: 720 }}>
        <div className="card stack">
          <h1 style={{ fontSize: 22 }}>Algo ha fallado en la interfaz</h1>
          <p className="muted">
            El asistente sigue funcionando en el servidor: esto solo afecta a la pantalla. Recarga la página; si se
            repite, comparte el detalle de abajo.
          </p>
          <pre className="mono xsmall" style={{ whiteSpace: 'pre-wrap', margin: 0 }}>
            {this.state.error.toString()}
          </pre>
          <div className="row">
            <button type="button" className="btn btn-primary" onClick={() => window.location.reload()}>
              Recargar
            </button>
            <button type="button" className="btn" onClick={() => this.setState({ error: null })}>
              Intentar de nuevo
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
      <App />
    </ErrorBoundary>
  </StrictMode>,
);
