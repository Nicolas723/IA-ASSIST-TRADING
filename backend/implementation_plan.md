# AI Trading Assistant — Arquitectura Profesional v2.0

> **Documento de arquitectura/plan original.** Para el estado real del código y la
> estructura de carpetas vigente, usa **`PROJECT_OVERVIEW.md`**. Este documento se
> mantiene como referencia de diseño; abajo se reconcilian las decisiones que
> cambiaron durante la implementación (ver *Estado Real de Implementación*).

---

## Decisiones Confirmadas

| Decisión | Elección |
|----------|----------|
| **LLM** | Multi-proveedor: OpenAI (primario) + Anthropic + Gemini + Ollama + OpenRouter |
| **Exchange Fase 1** | Binance (con interfaces para Bybit, OKX, Kraken, Coinbase) |
| **Dashboard** | Centro de control completo: lectura + control + auditoría |
| **Infraestructura** | Docker local → Hetzner → AWS/GCP (cloud-ready desde Fase 0) |
| **Base de datos** | PostgreSQL + TimescaleDB + Redis |
| **Backend** | Python 3.13 + FastAPI (async) |
| **Frontend** | React 19 + TypeScript + Vite + Vanilla CSS (glassmorphism) + gráficos SVG propios |
| **Rol de la IA** | Analista cuantitativo con razonamiento explicable (no "bot mágico") |
| **Trading automático** | Solo tras backtesting + paper trading validados + habilitación explícita |
| **Auditoría** | Total: prompt, respuesta, decisión, confianza, timestamp, agente |
| **Arquitectura** | Orientada a plugins: estrategias, indicadores, agentes, exchanges |
| **Observabilidad** | Desde Fase 0: logs estructurados, métricas, trazabilidad |
| **Seguridad** | API Keys cifradas, circuit breaker, límites diarios, dedup de órdenes |

---

## Estado Real de Implementación (2026-10-22)

> Reconciliación entre el plan original y el código actual.

| Área | Plan original | Realidad actual | Estado |
|------|---------------|-----------------|--------|
| Frontend | Next.js + React + Tailwind + TradingView | **React 19 + Vite + Vanilla CSS + SVG** (`MarketChart.tsx`, `BacktestCharts.tsx`) | ✅ (stack distinto) |
| Backend | Python 3.13 + FastAPI async | Python 3.13+ + FastAPI async, arquitectura hexagonal | ✅ |
| Datos | PostgreSQL + TimescaleDB + Redis | PostgreSQL/TimescaleDB + Redis + SQLAlchemy 2.0 async | ✅ |
| Ingesta | CCXT REST + WebSockets | CCXT REST + WS (precio real vía `fetch_ticker`) | ✅ |
| Indicadores | RSI/MACD/EMAs/BB/ATR | + SuperTrend, motor determinista | ✅ |
| Estrategias/Backtesting | Motor + optimización | `backtesting_engine.py`, `optimization_service.py`, plugins | ✅ |
| Riesgo | Circuit breaker, drawdown, sizing | `risk_service.py`, `position_sizer`, `drawdown_guard` | ✅ |
| Paper trading | Simulación completa | `PaperExchange` + journal durable + **rehidratación al reiniciar** | ✅ |
| Comité IA | Multi-agente + LLM opcional | Técnico, Price Action, Risk Manager, Coordinador (determinista; LLM opcional) | ✅ |
| Auditoría | `DecisionLog` total | `decision_logs` (payload JSON + columnas indexables) leído por `/api/v1/audit` | ✅ |
| Dashboard | Centro de control | React por pestañas (Asistente, Mercado, Cartera, Comité, Config) | ✅ |
| Trading real (Fase 8) | Binance/Bybit real + alertas | **Implementado**: ejecución real/testnet vía CCXT con interlocks, guardián SL/TP local, rehidratación/reconciliación del ledger real, idempotencia, alertas Telegram/webhook/email y panel UI | ✅ Completada |
| MLOps / aprendizaje (Fase 9) | Métricas + feedback continuo | Observabilidad básica; sin bucle de aprendizaje | ⏳ Pendiente |
| Infra prod | `docker-compose.prod.yml`, nginx/SSL | Solo `infra/docker/docker-compose.yml` (dev) | ⏳ Pendiente |

**Divergencias clave respecto al plan original**
- **No se usa Next.js/Tailwind/TradingView**: el frontend real es React 19 + Vite + CSS propio, con gráficos SVG (sin dependencia de TradingView).
- **Un único `docker-compose.yml`** (`infra/docker/`); los duplicados de `backend/` se eliminaron.
- **Auditoría**: se persiste el `CoordinatorDecision` completo en JSON (`payload`) en lugar de una columna por campo.

---

## Arquitectura Multi-LLM — Diseño Detallado

```
┌─────────────────────────────────────────────────────────────┐
│                    LLMGateway (Puerto)                       │
│  complete(prompt, model_config) → LLMResponse               │
└──────────────────────┬──────────────────────────────────────┘
                       │ implementado por
     ┌─────────────────┼──────────────────────────┐
     ▼                 ▼                ▼          ▼
┌─────────┐  ┌──────────────┐  ┌────────────┐ ┌────────┐
│ OpenAI  │  │  Anthropic   │  │   Gemini   │ │ Ollama │
│Adapter  │  │  Adapter     │  │  Adapter   │ │Adapter │
│GPT-5.5  │  │  Claude 4    │  │ 2.5 Flash  │ │Local   │
└─────────┘  └──────────────┘  └────────────┘ └────────┘
                                                    ▲
                                             ┌──────┴──────┐
                                             │ OpenRouter  │
                                             │  Adapter    │
                                             └─────────────┘

# Asignación de modelos por agente (configurable en .env / DB)
TechnicalAnalyst    → openai/gpt-4o          (razonamiento estructurado)
PriceActionAnalyst  → openai/gpt-4o          (análisis visual/patrones)
SentimentAnalyst    → google/gemini-flash     (velocidad + costo)
MacroAnalyst        → anthropic/claude-opus   (análisis largo, consistente)
FundamentalAnalyst  → anthropic/claude-opus   (análisis profundo)
RiskManagerAgent    → openai/gpt-4o          (precisión crítica)
StrategyOptimizer   → openai/o3              (razonamiento matemático)
SystemSupervisor    → ollama/llama3.2        (offline, privacidad)
PerformanceCoach    → anthropic/claude       (coaching, análisis histórico)
Embeddings          → ollama/nomic-embed     (local, privacidad, costo cero)
```

---

## Arquitectura de Plugins

```python
# Cada plugin es un módulo Python independiente que implementa interfaces del dominio

# Plugin de Estrategia
class MyCustomStrategy(BaseStrategy):          # Interfaz del dominio
    metadata = StrategyMetadata(
        name="my_strategy",
        version="1.0.0",
        author="trader",
        risk_level=RiskLevel.MEDIUM
    )
    async def generate_signal(self, context: MarketContext) -> Signal: ...

# Plugin de Indicador
class MyCustomIndicator(BaseIndicator):
    async def calculate(self, candles: list[Candle]) -> IndicatorResult: ...

# Plugin de Exchange
class MyExchangeAdapter(BaseExchangePort):
    async def place_order(self, order: Order) -> ExecutionReport: ...

# Plugin de Agente IA
class MyCustomAgent(BaseAgent):
    async def analyze(self, task: AgentTask) -> AgentOutput: ...

# Registro automático (discovery mediante entry_points o carpeta /plugins)
plugin_registry.register(MyCustomStrategy)
```

---

## Sistema de Auditoría Completa

```python
class DecisionLog(Base):
    id: UUID
    timestamp: datetime
    
    # Contexto
    symbol: str
    exchange: str
    timeframe: str
    
    # Datos utilizados
    candles_snapshot: JSONB      # OHLCV usados
    indicators_snapshot: JSONB   # Valores de indicadores
    portfolio_snapshot: JSONB    # Estado del portfolio
    
    # Agentes involucrados
    agents_invoked: list[str]
    agent_outputs: JSONB         # Output completo de cada agente
    
    # LLM Trazabilidad (cuando aplica)
    llm_provider: str | None
    llm_model: str | None
    prompt_sent: str | None      # Prompt exacto enviado
    llm_response: str | None     # Respuesta cruda del modelo
    
    # Decisión final
    final_signal: str            # BUY/SELL/HOLD
    confidence: float            # 0.0 - 1.0
    reasoning: str               # Explicación en lenguaje natural
    key_factors: list[str]
    risk_assessment: JSONB
    
    # Ejecución (si aplica)
    order_id: UUID | None
    execution_mode: str          # backtest/paper/real
    
    # Resultado (se actualiza después)
    outcome: str | None          # WIN/LOSS/BREAKEVEN/PENDING
    pnl_realized: Decimal | None
```

---

## Observabilidad desde Fase 0

```
Logging estructurado: structlog → JSON → stdout → agregador
Métricas:            prometheus-client → /metrics endpoint
Trazabilidad:        OpenTelemetry → Jaeger/Zipkin
Health checks:       /health, /ready, /live (Kubernetes-ready)
Profiling:           py-spy para producción
APM:                 Sentry (errores) + custom dashboards

Stack de observabilidad en Docker Compose:
├── Prometheus (scraping de métricas)
├── Grafana (dashboards)
├── Loki (agregación de logs)
└── Jaeger (trazabilidad distribuida)
```

---

## Seguridad — Implementación

```
API Keys:     Nunca en texto plano. Cifradas con Fernet (cryptography).
              En producción: HashiCorp Vault / AWS Secrets Manager.
              En desarrollo: .env local (excluido de git).

Dedup:        Redis SET con TTL para prevenir órdenes duplicadas.
              Hash = (symbol + side + quantity + price + timestamp_bucket)

Rate limiting: slowapi (FastAPI) + Redis como backend.

Circuit breaker:
  Nivel 1 — Por agente: timeout + retry con backoff exponencial
  Nivel 2 — Por coordinador: si ≥3 agentes fallan → HOLD forzado
  Nivel 3 — Por sistema: si DrawdownGuard dispara → suspensión total

Validación de datos:
  - Pydantic strict mode en todos los boundaries
  - Validación de rangos de precios (±20% del último precio)
  - Detección de datos corruptos o incompletos antes de análisis
```

---

## Estructura de Carpetas (objetivo del plan)

> ⚠️ Este árbol es el **objetivo del plan**, no el estado actual. La estructura
> vigente (y los archivos realmente existentes) está en **`PROJECT_OVERVIEW.md` §7**.
> Algunos elementos de abajo (`value_objects.py`, `audit_service.py`, `bybit.py`,
> `factory.py`, `messaging/event_bus.py`, `scheduler/tasks.py`,
> `interfaces/api/v1/settings.py`, `docker-compose.prod.yml`, `nginx/`)
> **no existen todavía**.

```
ai-trading-assistant/
│
├── backend/
│   ├── src/
│   │   ├── domain/
│   │   │   ├── market/
│   │   │   │   ├── entities.py          # Candle, Tick, OrderBook, Symbol
│   │   │   │   ├── value_objects.py     # Price, Volume, OHLCV
│   │   │   │   └── ports.py             # MarketDataPort (interfaz)
│   │   │   ├── trading/
│   │   │   │   ├── entities.py          # Order, Trade, Position, Portfolio
│   │   │   │   ├── value_objects.py     # OrderSide, OrderType, ExecutionMode
│   │   │   │   └── ports.py             # ExchangePort, OrderRepositoryPort
│   │   │   ├── strategy/
│   │   │   │   ├── base.py              # BaseStrategy (ABC)
│   │   │   │   ├── entities.py          # Signal, BacktestRun, BacktestResult
│   │   │   │   └── ports.py             # StrategyRepositoryPort
│   │   │   ├── risk/
│   │   │   │   ├── entities.py          # RiskProfile, DrawdownGuard
│   │   │   │   ├── value_objects.py     # RiskLevel, PositionSize
│   │   │   │   └── ports.py             # RiskManagerPort
│   │   │   ├── agents/
│   │   │   │   ├── base.py              # BaseAgent (ABC)
│   │   │   │   ├── entities.py          # AgentTask, AgentOutput, CoordinatorDecision
│   │   │   │   └── ports.py             # LLMGatewayPort
│   │   │   ├── indicators/
│   │   │   │   └── base.py              # BaseIndicator (ABC)
│   │   │   └── audit/
│   │   │       └── entities.py          # DecisionLog, AuditEvent
│   │   │
│   │   ├── application/
│   │   │   ├── commands/
│   │   │   │   ├── place_order.py
│   │   │   │   ├── start_strategy.py
│   │   │   │   └── request_analysis.py
│   │   │   ├── queries/
│   │   │   │   ├── get_portfolio.py
│   │   │   │   ├── get_signals.py
│   │   │   │   └── get_audit_log.py
│   │   │   ├── services/
│   │   │   │   ├── market_data_service.py
│   │   │   │   ├── trading_service.py
│   │   │   │   ├── risk_service.py
│   │   │   │   ├── strategy_service.py
│   │   │   │   └── audit_service.py
│   │   │   └── agents/
│   │   │       ├── coordinator.py       # AgentCoordinator
│   │   │       └── registry.py          # AgentRegistry (plugin system)
│   │   │
│   │   ├── infrastructure/
│   │   │   ├── persistence/
│   │   │   │   ├── database.py          # SQLAlchemy async engine
│   │   │   │   ├── models/              # SQLAlchemy ORM models
│   │   │   │   ├── repositories/        # Implementaciones de puertos
│   │   │   │   └── migrations/          # Alembic migrations
│   │   │   ├── exchange/
│   │   │   │   ├── ccxt_base.py         # Base adapter CCXT
│   │   │   │   ├── binance.py           # Binance adapter
│   │   │   │   ├── bybit.py             # Bybit adapter (stub)
│   │   │   │   ├── paper.py             # Paper trading mock
│   │   │   │   └── factory.py           # ExchangeFactory
│   │   │   ├── llm/
│   │   │   │   ├── gateway.py           # LLMGateway (router)
│   │   │   │   ├── openai_adapter.py
│   │   │   │   ├── anthropic_adapter.py
│   │   │   │   ├── gemini_adapter.py
│   │   │   │   ├── ollama_adapter.py
│   │   │   │   ├── openrouter_adapter.py
│   │   │   │   └── models.py            # LLMRequest, LLMResponse
│   │   │   ├── cache/
│   │   │   │   └── redis_client.py
│   │   │   ├── messaging/
│   │   │   │   └── event_bus.py         # Redis Streams
│   │   │   ├── market_data/
│   │   │   │   ├── rest_fetcher.py
│   │   │   │   └── ws_streamer.py
│   │   │   └── security/
│   │   │       └── key_manager.py       # Fernet encryption
│   │   │
│   │   ├── interfaces/
│   │   │   ├── api/
│   │   │   │   ├── v1/
│   │   │   │   │   ├── market.py
│   │   │   │   │   ├── trading.py
│   │   │   │   │   ├── portfolio.py
│   │   │   │   │   ├── strategy.py
│   │   │   │   │   ├── backtesting.py
│   │   │   │   │   ├── agents.py
│   │   │   │   │   ├── audit.py
│   │   │   │   │   ├── risk.py
│   │   │   │   │   └── settings.py      # Configuración desde UI
│   │   │   │   ├── deps.py
│   │   │   │   └── middleware.py        # Auth, rate limit, logging
│   │   │   ├── websocket/
│   │   │   │   ├── market_stream.py
│   │   │   │   └── agent_stream.py
│   │   │   └── scheduler/
│   │   │       └── tasks.py             # APScheduler periodic tasks
│   │   │
│   │   ├── plugins/                     # Plugin discovery folder
│   │   │   ├── strategies/              # Strategy plugins
│   │   │   ├── indicators/              # Indicator plugins
│   │   │   └── agents/                  # Agent plugins
│   │   │
│   │   └── shared/
│   │       ├── config/
│   │       │   └── settings.py          # Pydantic BaseSettings
│   │       ├── logging/
│   │       │   └── setup.py             # structlog configuration
│   │       ├── observability/
│   │       │   ├── metrics.py           # Prometheus metrics
│   │       │   └── tracing.py           # OpenTelemetry
│   │       ├── exceptions/
│   │       │   └── domain.py            # DomainError hierarchy
│   │       └── types/
│   │           └── common.py            # Decimal, UUID, Timestamp aliases
│   │
│   ├── tests/
│   │   ├── unit/
│   │   ├── integration/
│   │   ├── e2e/
│   │   └── conftest.py
│   │
│   ├── pyproject.toml
│   ├── Dockerfile
│   ├── Dockerfile.dev
│   └── .env.example
│
├── frontend/
│   ├── src/
│   │   ├── App.tsx                      # SPA por pestañas (bot, market, portfolio, agents, settings)
│   │   ├── App.css                      # Estilos (glassmorphism, Vanilla CSS)
│   │   ├── main.tsx
│   │   ├── components/
│   │   │   ├── MarketChart.tsx          # Gráfico de velas (SVG)
│   │   │   └── BacktestCharts.tsx       # Curvas de backtesting (SVG)
│   │   └── assets/
│   ├── package.json
│   └── Dockerfile.dev
│
├── infra/
│   └── docker/
│       ├── docker-compose.yml           # Stack dev: postgres, redis, backend, prometheus, grafana
│       ├── postgres/init.sql            # TimescaleDB + extensiones
│       ├── prometheus/prometheus.yml
│       ├── grafana/dashboards/
│       └── collector/collector.yaml
│
└── .github/
    └── workflows/
        └── ci.yml
```

---

## Roadmap — Estado

| Fase | Ámbito | Estado |
|------|--------|--------|
| ✅ Fase 0 | Fundaciones: FastAPI, capas hexagonales, Settings, structlog, Alembic, vault, plugins | **Completada** |
| ✅ Fase 1 | Ingesta de datos: CCXT REST + WebSocket, velas/ticks en TimescaleDB | **Completada** |
| ✅ Fase 2 | Motor de indicadores: RSI, MACD, EMAs, Bollinger, ATR, SuperTrend | **Completada** |
| ✅ Fase 3 | Estrategias + backtesting + optimización | **Completada** |
| ✅ Fase 4 | Gestión de riesgo: circuit breaker, drawdown guard, position sizing | **Completada** |
| ✅ Fase 5 | Paper trading: fills, PnL, SL/TP, journal durable y rehidratación | **Completada** |
| ✅ Fase 6 | Sistema multiagente IA (comité + coordinador determinista) | **Completada** |
| 🟡 Fase 7 | Dashboard web: pestañas operativas; faltan tests de frontend | **Parcial** |
| ✅ Fase 8 | Trading real + alertas | **Completada** |
| ✅ Fase 8.1 | Endurecimiento real: rehidratación, reconciliación, idempotencia y smoke test | **Completada** |
| ⏳ Fase 9 | MLOps + aprendizaje continuo | **Pendiente** |

---

*Estado: Fases 0–6, 8 y 8.1 completadas; Fase 7 parcial (sin tests de frontend); Fase 9 pendiente — 2026-10-22*
