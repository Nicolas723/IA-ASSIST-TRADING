# 🤖 AI Trading Assistant — Documento de Traspaso (Handover)

> **Documento para continuar el desarrollo en una nueva conversación / entorno.**  
> Resume la arquitectura, estado actual del proyecto, credenciales, decisiones de diseño y las tareas pendientes exactas.

---

## 📌 1. Visión General del Proyecto
**AI Trading Assistant** es una plataforma de trading algorítmico y cuantitativo asistida por Inteligencia Artificial y un **Comité Multi-Agente**.
- **Modo Paper Trading (Virtual)**: Simulación completa con $10,000 USDT virtuales, cálculo de comisiones, slippage, PnL en tiempo real y Stop Loss / Take Profit automáticos.
- **Modo Real / Testnet**: Conexión con exchanges (Binance / Bybit) mediante CCXT y almacenamiento seguro de claves cifradas con AES-256 en un Credential Vault.
- **Frontend Moderno e Intuitivo**: Hecho en React + Vite + Vanilla CSS, 100% en español. La vista principal reúne gráfico y asistente autónomo; la interfaz no expone compra/venta manual. Las métricas técnicas se agrupan bajo “Avanzado”.

---

## 🏗️ 2. Arquitectura y Tecnologías

### **Backend (Python 3.13 / FastAPI)**
- **Framework**: FastAPI con arquitectura hexagonal / Clean Architecture (Domain, Application, Infrastructure, Interfaces).
- **Base de Datos**: PostgreSQL 16 + **TimescaleDB** para series temporales (velas OHLCV, ticks). SQLAlchemy 2.0 (asyncio + asyncpg).
- **Caché y Mensajería**: Redis 7 (FastAPI cache, colas, rate limiting).
- **Datos de Mercado**: Conector CCXT (Binance público para velas y ticks en vivo sin requerir API key para paper trading).
- **WebSockets**: Streaming en vivo hacia el frontend (`/api/v1/ws/market/{symbol}`).
- **Comité Multi-Agente**:
  - `TechnicalAnalyst`: Analiza RSI, MACD, EMAs (9, 21, 50, 200), Bandas de Bollinger.
  - `PriceActionAnalyst`: Soporte/Resistencia, patrones de velas (Pin bar, envolvente) y conceptos SMC (Smart Money Concepts).
  - `RiskManager`: Volatilidad ATR, sizing de posición dinámico y colocación de Stop-Loss / Take-Profit (mínimo ratio 2.0:1).
  - `AgentCoordinator`: Pondera los votos de los especialistas y genera veredicto de consenso con nivel de convicción.
  - *Nota*: El comité usa el proveedor principal configurado y admite fallback a Gemini, Anthropic, OpenRouter, DeepSeek y Ollama. Los proveedores con cuota agotada, saldo insuficiente o credenciales rechazadas quedan en cooldown y no consumen peticiones. Sin proveedores configurados conserva análisis determinista; sus resultados aún no entrenan automáticamente los modelos.

### **Frontend (TypeScript / React 19 / Vite)**
- **UI & Diseño**: Vanilla CSS moderno con glassmorphism, modo oscuro con gradientes cuidados, micro-animaciones y diseño adaptable (desktop y móvil).
- **Organización de la interfaz**:
  1. `Asistente`: gráfico interactivo de velas y precio vivo, selección de par/temporalidad, señal del comité en lenguaje sencillo y controles para iniciar/pausar el bot autónomo.
  2. `Configuración`: conexión con Binance y ajustes principales.
  3. `Avanzado`: comité de analistas, MLOps, estrategia, límites, alertas y diagnóstico. La interfaz de operación manual y la pestaña de cartera se retiraron del recorrido normal.

### **Infraestructura (Docker Compose)**
Ubicación: `infra/docker/docker-compose.yml`
- `ata-backend` (FastAPI en `:8000`)
- `ata-postgres` (TimescaleDB en `:5432`)
- `ata-redis` (Redis en `:6379`)
- `ata-prometheus` (Métricas en `:9091`)
- `ata-grafana` (Dashboards en `:3001` - user: `admin`, pass: `admin_dev`)

> ℹ️ El único `docker-compose.yml` del proyecto es **`infra/docker/docker-compose.yml`** (los duplicados obsoletos de `backend/` se eliminaron).

---

## ⚙️ 3. Configuración y Credenciales Clave

### Base de Datos (`backend/.env` y Compose)
- **Host**: `localhost` (o `postgres` dentro de la red Docker)
- **Puerto**: `5432`
- **Base de datos**: `trading_db`
- **Usuario**: `trading_user`
- **Contraseña**: `trading_password_dev`
- **DATABASE_URL**: `postgresql+asyncpg://trading_user:trading_password_dev@localhost:5432/trading_db` (en local) o `postgresql+asyncpg://trading_user:trading_password_dev@postgres:5432/trading_db` (en Docker).
*IMPORTANTE*: Si el volumen ya existía, la contraseña interna de PostgreSQL fue sincronizada con:
```sql
ALTER USER trading_user WITH PASSWORD 'trading_password_dev';
```

> ⚠️ **Credenciales solo para desarrollo local.** Rota estos valores antes de cualquier despliegue. Las claves API de exchange se guardan cifradas (AES-256) en el Credential Vault, nunca en texto plano.

> 📁 **Portabilidad de rutas.** La configuración y los archivos de datos se resuelven **relativos al código**, no al directorio de trabajo:
> - `backend/.env` se localiza desde `settings.py` (funciona aunque arranques el backend desde otra carpeta).
> - El vault se guarda por defecto en `backend/.vault_store.json`; puedes cambiarlo con la variable de entorno `VAULT_STORAGE_PATH`.
> - `.vault_store.json` está en `.gitignore` (nunca se versiona).

---

## 🚀 4. Cómo Iniciar el Proyecto

> Todas las rutas son **relativas a la raíz del repositorio** (`ai-trading-assistant/`). No hay rutas absolutas de máquina, así que el proyecto funciona aunque se mueva de carpeta, de usuario o de equipo.

### Paso 1: Levantar los contenedores de Docker
```powershell
# Opción A — script portable (funciona desde cualquier carpeta)
.\scripts\dev-up.ps1

# Opción B — manual (siempre ejecutado desde la raíz del repositorio)
docker compose -f infra/docker/docker-compose.yml up -d
```
Verificar que todos estén sanos:
```powershell
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
```

### Paso 2: Levantar el Frontend
```powershell
# Opción A — script portable
.\scripts\dev-frontend.ps1

# Opción B — manual (desde la raíz del repositorio)
npm --prefix frontend run dev
```
Abrir en el navegador: [http://localhost:5173](http://localhost:5173)

Usuarios de Linux/macOS/WSL: los equivalentes están en `scripts/dev-up.sh` y `scripts/dev-frontend.sh`.

### Paso 3: Probar los Endpoints Principales
- Estado Backend: `GET http://localhost:8000/api/v1/status`
- Velas de Mercado: `GET http://localhost:8000/api/v1/market/BTC%2FUSDT/candles?timeframe=1h&limit=5`
- Estado del Bot Autónomo: `GET http://localhost:8000/api/v1/autonomous/status`
- Cartera Virtual: `GET http://localhost:8000/api/v1/portfolio`

---

## ✅ 5. Bug de "precio estático" — RESUELTO

### ⚠️ Problema reportado por el usuario:
> *"Actualmente el bot todo el tiempo está indicando el mismo precio de moneda, pero en la gráfica veo que el dato varía, creo que el bot no está tomando los datos correctamente."*

### Causa raíz (eran DOS problemas independientes):
1. **El bot evaluaba con la vela cerrada de 1h.** En `application/services/autonomous_engine.py`, `_execute_cycle()` obtenía el precio con:
   ```python
   candles = await self.market_service.get_candles(...)
   current_price = float(candles[-1].close)
   ```
   Como una vela de 1h solo se cierra cada 60 minutos, `candles[-1].close` quedaba **congelado** durante toda la hora (y con más razón porque el `current candle` nunca se actualizaba).
2. **El WebSocket del gráfico NO usaba el mercado real.** En `interfaces/websocket/market_stream.py`, `_ticker_loop()` generaba **precios aleatorios simulados** a partir de precios base hardcodeados (`BTC/USDT: 84800.0`, etc.) con `random.uniform(-0.0005, 0.0005)`. Por eso la gráfica "se movía" pero no reflejaba el mercado ni coincidía con el bot.

> La nota anterior del handover decía que el WebSocket ya emitía fluctuaciones reales de CCXT. **Era incorrecto**: eran valores sintéticos.

### Solución implementada (precio en tiempo real, una sola fuente de verdad):
1. **`domain/market/ports.py`** → nuevo método abstracto `fetch_last_price(symbol) -> Decimal` en `MarketDataPort`.
2. **`infrastructure/exchange/ccxt_base.py`** → implementado con `fetch_ticker` de CCXT y devuelve el campo `last` (con manejo de `last=None` → `ExchangeError`).
3. **`infrastructure/market_data/rest_fetcher.py`** → `RetryingMarketDataFetcher.fetch_last_price()` con reintentos/backoff.
4. **`application/services/market_data_service.py`** → nuevo `async get_latest_price(symbol) -> Decimal` (snapshot instantáneo, NO depende de la vela cerrada).
5. **`application/services/autonomous_engine.py`** → `_execute_cycle()` ahora usa `get_latest_price()` y **solo cae al cierre de vela como fallback** si el ticker falla temporalmente (el daemon nunca se cae).
6. **`interfaces/websocket/market_stream.py`** → `ConnectionManager.set_price_provider()` + `_resolve_price()`: el stream emite el **precio real** cuando hay proveedor y usa la simulación solo como fallback.
7. **`main.py`** → en el `lifespan` se conecta el proveedor del WebSocket al ticker real (`fetch_last_price`).

### Resultado:
- El bot y el gráfico usan ahora la **misma fuente de precio en tiempo real**, por lo que ambos cambian segundo a segundo y coinciden.
- Fallback robusto: si el exchange no responde, el bot sigue operando con el cierre de vela y el WS con un valor simulado.

### Pruebas:
- `tests/unit/test_services.py::test_market_data_service_get_latest_price`
- `tests/unit/test_autonomous_engine.py::test_autonomous_cycle_uses_live_ticker_price`
- `tests/unit/test_websocket_stream.py::test_connection_manager_resolve_price_uses_live_provider` y `..._falls_back_to_simulation`

---

## 💾 6. Persistencia y Auditoría (capa añadida)

### Antes
Solo se **persistían las velas** (`candles`, `ticks`). Órdenes, posiciones, cartera y las decisiones del Comité IA vivían **únicamente en memoria**: al reiniciar el backend se perdía todo el historial operativo y la "auditoría total" prometida en el plan no era real.

### Ahora
Se añadió una capa de persistencia completa (PostgreSQL / TimescaleDB):

| Tabla | Contenido |
|---|---|
| `orders` | Órdenes en cualquier modo (paper/backtest/real) |
| `trades` | Ejecuciones completadas |
| `positions` | **Posiciones abiertas actuales** (upsert por id; se borran al cerrar) |
| `portfolio_snapshots` | Serie temporal de equity/balances y drawdown |
| `decision_logs` | **Auditoría IA completa**: veredicto, confianza, consenso, riesgo, targets y el `CoordinatorDecision` íntegro en JSON |

### Componentes
- **Puertos de dominio**: `domain/trading/ports.py` (`TradingRepositoryPort`) y `domain/agents/ports.py` (`DecisionRepositoryPort`).
- **Repositorios**: `infrastructure/persistence/repositories/trading_repository.py` (`SqlTradingRepository`) y `.../decision_repository.py` (`SqlDecisionRepository`), con mappers puros dominio↔modelo (Decimal sin pérdida de precisión).
- **Modelos ORM**: `persistence/models/trading.py` y `.../audit.py`.
- **Migración Alembic**: `c4f1a2b3d5e6_trading_journal_and_audit` (encadena desde `93164c44d6de`).

### Wiring (ya activo)
- **`AgentService.run_analysis()`** guarda cada `CoordinatorDecision` en `decision_logs` (best-effort: si la BD falla, se registra warning y no rompe el análisis).
- **`GET /api/v1/audit/decisions`** ahora lee de la BD (con filtro `symbol`) y cae a la memoria histórica si la BD no está disponible.
- **`POST /api/v1/trading/orders`** y **`.../positions/{id}/close`** escriben la orden + sincronizan posiciones en el journal.
- **`GET /api/v1/trading/orders/history`** (nuevo) lee el historial durable de órdenes.

### Pruebas
- `tests/unit/test_persistence.py` (15 tests) — round-trip de órdenes/trades/posiciones/cartera y de la decisión IA completa (incl. `agent_outputs`, `prompt_sent`, `llm_response`), sincronización de posiciones, limpieza del journal, **rehidratación** y consulta de velas, contra **SQLite en memoria** (`aiosqlite`, nueva dep de dev). No requiere PostgreSQL.
- `tests/unit/test_strategy_plugins.py` (11 tests) — señales BUY/SELL/HOLD y variantes de `MarketContext` de los plugins EMA crossover, MACD momentum y RSI mean reversion.

### Rehidratación de `PaperExchange` (RESUELTO)
El exchange virtual sigue siendo en memoria, pero ahora **se reconstruye desde la BD al arrancar**:
- `PaperExchange.restore(...)` reemplaza su estado interno (posiciones, órdenes, trades, `available_balance`, `realized_pnl`, `peak_equity`).
- `application/services/hydration.py` → `hydrate_paper_exchange()` lee el journal y el último `portfolio_snapshot`, y `hydrate_from_database()` abre su propia sesión.
- `main.py` (lifespan) llama a la rehidratación al iniciar; si la BD no está disponible, registra un warning y arranca con cuenta limpia.
- **Journaling unificado** en `application/services/journal.py` (`record_execution`): lo usan el router HTTP **y** el motor autónomo (vía callback inyectado en `deps`), de modo que las órdenes del bot también persisten.
- El **reset de cartera** (`POST /api/v1/portfolio/reset`) limpia el journal para que un reinicio no rehidrate la cuenta antigua.
- El motor también refresca el `current_price` de las posiciones abiertas en cada ciclo (PnL no realizado siempre al día).

---

## 📂 7. Estructura de Carpetas Principal

```text
ai-trading-assistant/
├── PROJECT_OVERVIEW.md
├── .gitignore
├── scripts/                                    # Arranque portable (rutas resueltas desde el script)
│   ├── dev-up.ps1 / dev-up.sh                  # Levanta el stack Docker
│   └── dev-frontend.ps1 / dev-frontend.sh      # Levanta el frontend
├── backend/
│   ├── .env / .env.example                    # Variables de entorno (credenciales solo-dev)
│   ├── alembic.ini                            # Config de migraciones (script_location -> src/.../migrations)
│   ├── pyproject.toml
│   ├── src/
│   │   ├── main.py                            # Entrypoint FastAPI (lifespan, routers, middleware)
│   │   ├── application/
│   │   │   ├── services/
│   │   │   │   ├── autonomous_engine.py        # Daemon del bot (precio en tiempo real + fallback a vela)
│   │   │   │   ├── agent_service.py            # Orquestación del Comité Multi-Agente
│   │   │   │   ├── market_data_service.py      # Ingesta de velas + get_latest_price() (ticker live)
│   │   │   │   ├── trading_service.py          # Órdenes, PnL, posiciones (paper/real)
│   │   │   │   ├── journal.py                  # Persistencia unificada de ejecuciones (router + bot)
│   │   │   │   ├── hydration.py                # Rehidrata PaperExchange desde la BD al arrancar
│   │   │   │   ├── risk_service.py             # Circuit breaker y drawdown limits
│   │   │   │   ├── vault_service.py            # Cifrado AES-256 de claves API
│   │   │   │   ├── indicator_service.py        # RSI, MACD, EMAs, Bollinger, ATR, SuperTrend
│   │   │   │   ├── strategy_service.py         # Registro de estrategias
│   │   │   │   ├── backtesting_engine.py       # Motor de backtesting
│   │   │   │   ├── optimization_service.py     # Optimización de parámetros
│   │   │   │   └── observability_service.py    # Métricas / MLOps
│   │   ├── domain/                             # Entidades puras y reglas de negocio
│   │   │   ├── agents/
│   │   │   │   ├── coordinator.py              # Pondera votos → veredicto de consenso
│   │   │   │   ├── base.py / entities.py / ports.py
│   │   │   │   └── specialists/                # technical_analyst, price_action_analyst, risk_manager
│   │   │   ├── market/                         # entities.py + ports.py (incl. fetch_last_price)
│   │   │   ├── indicators/                     # builtin.py (indicadores deterministas)
│   │   │   ├── optimization/                   # parameter_optimizer, performance_coach
│   │   │   ├── risk/                           # position_sizer, drawdown_guard
│   │   │   ├── strategy/                       # base.py, entities.py
│   │   │   └── trading/                        # entities.py + ports.py (TradingRepositoryPort)
│   │   ├── infrastructure/
│   │   │   ├── exchange/                       # ccxt_base (fetch_last_price), binance, live_binance, paper
│   │   │   ├── market_data/rest_fetcher.py     # Decorador de reintentos/backoff
│   │   │   ├── persistence/                    # database.py + models/ (market, trading, audit)
│   │   │   │                                   #   + repositories/ (candle, trading, decision)
│   │   │   │                                   #   + migrations/ (Alembic; c4f1a2b3d5e6 = journal + auditoría)
│   │   │   ├── llm/                            # gateway + adapters (openai, anthropic, gemini, ollama, openrouter, deepseek)
│   │   │   ├── security/                       # credential_vault, key_manager
│   │   │   └── cache/redis_client.py
│   │   ├── interfaces/
│   │   │   ├── api/v1/                         # routers: market, trading, portfolio, autonomous, agents,
│   │   │   │                                   #   risk, strategy, backtesting, vault, observability,
│   │   │   │                                   #   audit, migration, status + deps.py
│   │   │   └── websocket/market_stream.py      # Ticker en vivo (precio real con fallback simulado)
│   │   ├── plugins/                            # strategies/ e indicators/ (auto-descubiertos)
│   │   └── shared/                             # config, logging, exceptions, observability, plugins, types
│   └── tests/
│       ├── unit/                               # 66 tests unitarios (72.7% cobertura), incl. test_persistence.py y test_strategy_plugins.py
│       └── integration/
├── frontend/
│   ├── src/
│   │   ├── App.tsx                             # Dashboard por pestañas en español
│   │   ├── App.css                             # Estilos glassmorphism / modo oscuro
│   │   ├── components/
│   │   │   ├── MarketChart.tsx                 # Gráfico SVG de velas + línea de precio en vivo
│   │   │   └── BacktestCharts.tsx              # Curva de equidad
│   │   └── main.tsx
│   └── package.json
├── infra/
│   └── docker/
│       ├── docker-compose.yml                  # Postgres/TimescaleDB, Redis, Backend, Prometheus, Grafana
│       └── prometheus/                         # Configuración de Prometheus
└── docs/
```

---

## 🧪 8. Calidad y CI

- **Tests**: **149 unitarios + 11 de integración** pasando; cobertura **73.84%** (gate `--cov-fail-under=70`).
  - El job de CI ya **no filtra con `-m unit`**: antes descartaba 41 tests (corrían 14 de 66) y medía 57.8% de cobertura.
- **MyPy strict**: **0 errores** en 96 archivos (`explicit_package_bases = true`). Ahora es **bloqueante** en CI.
- **Ruff**: **0 hallazgos** y **116 archivos formateados**. `ruff check src tests` y `ruff format --check src tests` son **bloqueantes** en CI (backlog de estilo agotado).
  - `flake8-type-checking` respeta los modelos Pydantic/SQLAlchemy y los routers FastAPI, que evalúan anotaciones en runtime.
  - Alias de tipos migrados a sintaxis PEP 695 (`type Price = Decimal`).
- **Frontend**: job de CI con `npm ci` + `oxlint` + `tsc -b && vite build`.
- **Integración**: el job aplica `alembic upgrade head` antes de ejecutar los tests.
- **Correcciones de tipos relevantes**: el SL/TP del bot comparaba `PositionSide` con `OrderSide` (nunca se disparaba); corregido a `LONG`/`SHORT`.
- **Higiene**: un único `docker-compose.yml` (`infra/docker/`), eliminado el `MarketDataService` duplicado y `implementation_plan.md` alineado con la realidad (React/Vite, no Next.js).

### Fase 8 — Trading real y alertas (COMPLETADA)

**Ejecución real (Binance / Bybit vía CCXT)**
- `infrastructure/exchange/live_exchange.py`: adaptador genérico asíncrono con `ping`, balances, balance libre, órdenes market/limit/stop y mapeo de errores CCXT a excepciones de dominio.
- `application/services/live_trading_service.py`: ledger en memoria de órdenes, trades y posiciones reales; cartera calculada con el saldo USDT del exchange; cierre por posición o por símbolo (venta del saldo base libre).
- **Interlocks acumulativos**:
  1. `EXECUTION_MODE=real` **y** `ENABLE_REAL_TRADING=true`.
  2. Credenciales presentes en el Credential Vault.
  3. `ALLOW_MAINNET_TRADING=true` si las claves son de mainnet (por defecto solo testnet).
  4. `REQUIRE_MANUAL_CONFIRMATION=true` exige `confirm=true` en cada orden real.
  5. `REAL_TRADING_ALLOWED_SYMBOLS` (allowlist) y `REAL_TRADING_MAX_NOTIONAL` (límite por operación).
  6. `ENABLE_AUTONOMOUS_REAL_TRADING=false` por defecto; el daemon rechaza arrancar en real si no se habilita explícitamente.
  7. Validación previa con `RiskService` (stop-loss obligatorio, tamaño, drawdown y circuit breaker).
- **Protección SL/TP**:
  - **OCO nativo de Binance spot (Fase 8.2, opt-in)**: si `REAL_TRADING_OCO_ENABLED=true` y una orden BUY se llena con SL/TP, se coloca una OCO exchange-side (`POST /api/v3/order/oco`) con TP limit y SL stop-limit. La OCO protege aunque el backend esté apagado. Por defecto está apagada. Solo Binance spot; Bybit sigue usando el guardián local.
  - **Guardián local**: en el lifespan (`live-trading-guard`) consulta el precio y cierra con orden de mercado cuando se cruza el SL/TP. Es el fallback cuando la OCO no está disponible o falla su creación. El estado expone `protection_mode=exchange_oco|oco_with_local_fallback|oco_discovery_failed|local_guard`, `oco_enabled`, `oco_protected_positions` y `oco_discovery_failed_exchanges`.
  - **Ciclo de vida OCO**: se cancela antes de cualquier venta o cierre manual (incluido `POST /real/orders` con side SELL); si no se puede confirmar la cancelación, la venta se aborta para evitar una doble venta. Al añadir a una posición existente se cancela la OCO anterior y se recrea por la cantidad total. La reconciliación importa las salidas ejecutadas por la OCO al ledger/journal.
  - **Detección de ejecución**: `GET /api/v3/orderList` de Binance devuelve solo los `orderId` de las piernas, no su estado ni cantidad ejecutada. El adaptador consulta cada pierna (`GET /api/v3/order`) antes de declarar una OCO terminal sin fill. Si no puede verificar las piernas, falla de forma segura y mantiene la protección/guardián bloqueado para ese símbolo, evitando una doble venta.
  - **Reinicio**: al arrancar se descubren las OCO abiertas y se reasocian a las posiciones restauradas. Si el descubrimiento falla, el guardián local no opera ese exchange y las ventas/cierres se bloquean hasta confirmar que no hay una OCO activa (fail-safe).
  - **Stop-limit**: el SL de la OCO usa `REAL_TRADING_OCO_STOP_LIMIT_BUFFER_PCT=0.1` por defecto para dar margen al stop-limit. En huecos fuertes puede no llenarse; en ese caso la posición sigue bajo el guardián local y las alertas.
- **API**: `GET /api/v1/trading/real/status`, `GET /api/v1/trading/real/account`, `GET /api/v1/trading/real/positions`, `POST /api/v1/trading/real/orders`, `POST /api/v1/trading/real/positions/close`, `POST /api/v1/trading/real/reconcile`.
- **Control desde la app**: `GET/PUT /api/v1/trading/runtime-config` permite cambiar paper/real, testnet/mainnet, allowlist, notional máximo, OCO y permiso autónomo sin editar `.env` ni reiniciar. Los cambios se guardan en `backend/vault/.runtime_config.json` (ignorado por Git) y se aplican en caliente. Testnet es el entorno inicial; Mainnet exige claves propias, opt-in explícito adicional y confirmación, y no se puede cambiar de entorno con posiciones reales abiertas. La confirmación manual por orden permanece obligatoria en modo real.
- **Credential Vault**: almacena claves separadas por exchange y entorno, por lo que testnet y mainnet pueden coexistir; los archivos antiguos de una sola clave se migran conservando el entorno indicado en su metadata.
- **Persistencia**: las órdenes reales se escriben en el journal (`orders`, `trades`, `positions`, snapshot de cartera) mediante `persist_live_order_to_database`; el journaling sigue siendo best-effort.
- **Rehidratación real**: al arrancar se restauran posiciones, órdenes, trades y el último snapshot de equity desde PostgreSQL. Si hay posiciones restauradas pero el trading real o las credenciales no están activos, se emite una alerta crítica.
- **Reconciliación**: cada `REAL_TRADING_RECONCILE_INTERVAL_SECONDS` (300 s por defecto) se comparan las posiciones locales con el saldo base del exchange: se eliminan las cerradas fuera de la plataforma y se ajustan las cantidades parciales. También se puede forzar desde la UI o con `POST /real/reconcile`. Al arrancar se recuperan las órdenes propias (`ata-`) ausentes del registro, incluidas las cerradas dentro de `REAL_TRADING_ORDER_RECOVERY_LOOKBACK_SECONDS` (24 h por defecto), contabilizando cada fill una sola vez.
- **Gate de conciliación**: antes de abrir una entrada nueva en modo REAL, el motor ejecuta una sonda de solo lectura (`preflight_reconciliation`) contra el exchange. Si la sonda falla (red, credenciales o exchange no disponible), la entrada se suspende y se registra un error; no se abre riesgo nuevo con un ledger local potencialmente desincronizado. Las salidas (ventas y cierres por SL/TP) nunca se bloquean, para que una conciliación degradada no atrape una posición abierta.
- **Idempotencia**: cada orden real acepta `client_order_id` (máx. 36 caracteres). Se mapea a `newClientOrderId` en Binance y `orderLinkId` en Bybit. Un reintento con el mismo id devuelve la orden existente sin volver a enviarla al exchange.
- **Limitaciones conocidas**:
  - La OCO solo cubre Binance spot y una OCO por símbolo (si se añade a la posición se reemplaza). Si la creación o la cancelación falla, se alerta y se mantiene el guardián local cuando no hay OCO activa; si una OCO previa sigue activa, no se envía una venta local para evitar duplicarla.
  - Si la OCO se ejecuta con el backend apagado, al reiniciar la reconciliación de balances elimina la posición y la recuperación de órdenes cerradas reimporta la ejecución si cae dentro de la ventana configurada. Fuera de esa ventana el trade de salida puede requerir revisión manual del historial del exchange para un PnL exacto.
  - El guardián local solo protege mientras el backend está encendido. La rehidratación es best-effort y el exchange sigue siendo la fuente de verdad para balances; revisar posiciones tras un reinicio o una intervención manual.

**Alertas multicanal**
- `domain/notifications` + `application/services/alert_service.py` con severidades (`info`, `success`, `warning`, `critical`), filtro por `ALERT_MIN_SEVERITY`, contadores y entrega best-effort (nunca rompe una operación).
- Canales: Telegram (`TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`), webhook JSON (`ALERT_WEBHOOK_URL`) y email SMTP (`EMAIL_SMTP_HOST`, `EMAIL_FROM`, `EMAIL_TO`, ...).
- Eventos: órdenes reales ejecutadas, cierres, SL/TP, kill-switch, bloqueos de riesgo y arranque con trading real.
- **API**: `GET /api/v1/alerts/status`, `POST /api/v1/alerts/test`.
- **Frontend**: panel "Trading Real y Alertas" en Configuración con entorno (testnet/mainnet), credenciales, límite, guardián, modo de protección, contador de OCO activas, símbolos, estado de canales, botón de alerta de prueba y botón "Reconciliar con exchange". El botón de compra/venta usa los endpoints reales cuando el modo REAL está activo (con confirmación por orden y `client_order_id`).

**Pruebas**: incluye `tests/unit/test_runtime_config_service.py` (persistencia, confirmación y restricciones), vault por entorno, gateway LLM/fallback/cooldowns/timeout, guard de correlación y salvaguardas del asistente autónomo. Verificación local vigente: **344 tests aprobados**, cobertura **79,59 %**, Ruff/MyPy y formateo sin errores; frontend compila con `npm run build` (Oxlint mantiene avisos de hooks preexistentes).

### Endurecimiento operativo (2026-10-22)

- **Git**: repositorio inicializado con baseline de la Fase 8 (`752a0b2`) y rama `feat/phase8-hardening` para los cambios de endurecimiento. `.env`, `.vault_store.json`, `.venv`, `node_modules`, cobertura y cachés quedan ignorados.
- **Rehidratación real**: `hydrate_live_from_database` restaura el ledger real al arrancar; el guardián arranca después. Si no puede proteger las posiciones, alerta.
- **Reconciliación**: `LiveTradingService.reconcile_positions` compara cada posición con el saldo base del exchange, elimina posiciones cerradas fuera y ajusta cantidades. Se ejecuta en el guardián y bajo demanda.
- **Idempotencia**: `client_order_id` en `POST /real/orders`, deduplicación en memoria + journal y mapeo por exchange.
- **Guard más robusto**: un fallo al cerrar por SL/TP ya no se traga silenciosamente; se registra y se emite alerta crítica, manteniendo la posición para reintentar.
- **Smoke test manual**: `scripts/real_testnet_smoke.py` (dry-run por defecto; exige `--execute --yes`; bloquea mainnet sin `--allow-mainnet`). Con `REAL_TRADING_OCO_ENABLED=true` también reporta el estado de la OCO y advierte si no se adjuntó a la posición. Ejemplo: `backend\.venv\Scripts\python.exe scripts\real_testnet_smoke.py --execute --yes --quantity 0.0001`.
- **Verificación**: 163 tests (152 unitarios + 11 integración), cobertura 75.55%, Ruff 0 hallazgos, MyPy 0 errores, frontend build OK.
- **OCO exchange-side implementada y validada (Fase 8.2)**: Binance spot, flag `REAL_TRADING_OCO_ENABLED=false` por defecto, cancelación segura antes de cierres, reconciliación de ejecuciones y descubrimiento al reiniciar.
- **Validación testnet (2026-10-23)**: OCO creada y redescubierta tras reiniciar; cierre manual cancela la OCO; pierna TP ejecutada y registrada en el journal como orden `limit` con nota `Binance OCO ... executed (LIMIT_MAKER)`, sin venta adicional del guardián local. La prueba también reveló y corrigió que Binance no incluye el estado de las piernas en `orderList`; ahora se consultan una a una antes de concluir que no hubo fill.

### Fase 9 — Aprendizaje continuo, régimen y activación Testnet (2026-10-24)

- **Activación real/Testnet explícita**: `PUT /api/v1/trading/runtime-config` con `confirm=true` dejó `execution_mode=real`, `enable_real_trading=true`, `trading_environment=testnet`, `enable_autonomous_real_trading=true`, `require_manual_confirmation=true` y `allow_mainnet_trading=false`. `GET /real/status` confirmó `enabled=true`, `testnet=true`, credenciales Testnet y `open_positions=0`. Mainnet sigue bloqueado y el bot autónomo **no** se inicia solo.
- **Smoke real en Testnet**: `scripts/real_testnet_smoke.py --execute --yes --close --quantity 0.0001` ejecutó compra y venta market reales en Binance Testnet (`BTC/USDT`, 0.0001), abrió y cerró la posición, y la alerta de prueba de Telegram se entregó. No se usó dinero real.
- **Detector de régimen (solo lectura)**: `GET /api/v1/market/{symbol}/regime?timeframe=1h&limit=200` clasifica tendencia (`uptrend`/`downtrend`/`range`) y volatilidad (`low`/`normal`/`high`) con EMA20/50, pendiente y percentil de ATR14. Solo usa velas cerradas, marca datos insuficientes/antiguos y publica `descriptive_only=true`, `affects_trading=false` y `execution_enabled=false`. El panel «Régimen de Mercado» de la vista principal muestra el resultado y no modifica señales, riesgo ni ejecución. Cada candidato del escaneo shadow incluye `regime_status`, `trend_regime` y `volatility_regime` como contexto descriptivo; el movimiento observado sigue limitado a la ventana solicitada.
- **Concentración y correlación (solo lectura)**: `GET /api/v1/risk/concentration?symbols=BTC/USDT,ETH/USDT&timeframe=1h&lookback=200` expone la exposición por activo frente a `MAX_POSITION_SIZE_PCT`, la exposición bruta frente a `MAX_PORTFOLIO_EXPOSURE_PCT` y la correlación de Pearson de retornos alineados por timestamp (mínimo 30 muestras comunes). El panel de riesgo muestra el desglose, la correlación más alta y advertencias. Devuelve `read_only=true`/`affects_trading=false`: no bloquea ni dimensiona órdenes. Con un solo símbolo permitido la correlación no aporta diversificación y el informe lo indica.
- **Sizing con exposición existente**: `PositionSizeRequest` acepta la exposición actual por activo y la bruta, y `PositionSizer` recorta la cantidad a la capacidad restante antes de emitir la orden; si la capacidad es cero, invalida el setup. El preflight de Paper y Live sigue aplicando los mismos límites como última barrera, de modo que el sizing y el guard no se contradicen. `AGENT_*_PROVIDER` quedó documentado como configuración obsoleta sin efecto: el comité usa siempre la cadena global primario/fallback.
- **Guard de correlación en el sizing**: antes de abrir una compra, el motor compara los retornos de velas cerradas del candidato con los de cada otra posición abierta. El spot es long-only, así que solo penaliza correlación positiva (la negativa diversifica). Con `CORRELATION_GUARD_ENABLED=true` (por defecto), desde `CORRELATION_REDUCE_THRESHOLD=0.75` reduce el riesgo linealmente hasta `CORRELATION_BLOCK_THRESHOLD=0.95`, con suelo del 25 %, y desde 0.95 bloquea la entrada con un `WARNING`. Exige `CORRELATION_MIN_SAMPLES=30` retornos comunes y usa `CORRELATION_LOOKBACK=200` velas; si no hay datos suficientes o falla la descarga, no penaliza y lo registra. Es un control de diversificación, no una probabilidad ni una previsión. El endpoint `/api/v1/risk/concentration` sigue siendo solo lectura.
- **Recorte al notional máximo**: el motor autónomo ajusta la cantidad de una orden real a `REAL_TRADING_MAX_NOTIONAL` antes de enviarla y registra un aviso; el servicio live mantiene el límite como última barrera. Esto evita que el sizing por riesgo (~5.000 USDT) bloqueara todas las órdenes con el límite de 50 USDT.
- **Claves LLM desde `.env`**: los `BaseSettings` anidados (`OpenAISettings`, `GeminiSettings`, `RiskSettings`, etc.) ahora cargan `_ENV_FILE_PATH`. Antes solo leían variables de proceso, así que el contenedor veía `OPENAI_API_KEY=EMPTY` aunque `/app/.env` tuviera las claves, y el comité caía siempre al análisis determinista.
- **Proveedor y modelo validados**: `LLM_PRIMARY_PROVIDER=deepseek` con `DEEPSEEK_DEFAULT_MODEL=deepseek-chat` y Google (`GOOGLE_DEFAULT_MODEL=gemini-3.8-flash`) como fallback. Una llamada real al comité con Google devolvió `llm_provider=google` en técnico y acción del precio, sin fallback, con ~3,5k tokens. DeepSeek acepta la clave (`GET /models` 200) pero devuelve `402 Insufficient Balance` hasta que se recargue la cuenta; OpenAI responde `insufficient_quota` (sin créditos).
- **SDK de Gemini y DeepSeek**: el adaptador de Google migró a `google-genai` (el paquete `google-generativeai` está descontinuado) y usa el cliente asíncrono. Se añadió `deepseek` como proveedor compatible con OpenAI (`DEEPSEEK_API_KEY`, `DEEPSEEK_BASE_URL`, `DEEPSEEK_DEFAULT_MODEL=deepseek-chat`), reutilizando el transporte de OpenAI con etiquetas de proveedor propias. La cuota gratuita de `gemini-3.8-flash` es de 20 peticiones/día por modelo y se reinicia a medianoche del Pacífico (≈07:00–08:00 UTC); al agotarse, el gateway cae al análisis determinista hasta el reinicio o hasta que se configure otra clave/proveedor. Los adaptadores ya no reintentan un `429` (solo timeouts y errores de conexión), porque reintentar una cuota diaria agotada consumía el presupuesto de peticiones sin posibilidad de éxito.
- **Cooldown de proveedores**: un `429` (con `retryDelay` de Gemini si viene), un `402` de saldo insuficiente o un `401/403` de credenciales aparcan el proveedor en el gateway (`provider_cooldowns`, visible en `/api/v1/agents/llm/status` y en el panel de coste IA). Las llamadas siguientes lo omiten sin gastar intentos; si todos están en cooldown, el comité falla rápido al análisis determinista en lugar de golpear la misma pared. Los cooldowns viven en memoria y no se reinician al empezar un ciclo.
- **Tope duro por llamada LLM**: `LLM_REQUEST_TIMEOUT_SECONDS=60` acota cada intento contra un proveedor. Si un SDK no responde (por ejemplo, una petición HTTP colgada), el gateway cancela la llamada, la contabiliza como timeout y prueba el siguiente proveedor en vez de dejar el ciclo autónomo bloqueado. Es independiente de los cooldowns: el cooldown evita reintentar un proveedor que ya falló; el tope evita esperar indefinidamente a uno que no contesta.
- **Cadencia del comité**: `llm_analysis_interval_seconds` (30 min por defecto) limita la frecuencia del análisis LLM completo. El daemon sigue gestionando posiciones y salidas cada `interval_seconds` (300 s), pero no gasta cuota en análisis de entrada en cada ciclo corto. Con 0 se analiza en cada ciclo.
- **Presupuestos LLM**: `LLM_DAILY_TOKEN_BUDGET=200000`, `LLM_CYCLE_TOKEN_BUDGET=12000`, `LLM_DAILY_REQUEST_BUDGET=18` y `LLM_CYCLE_REQUEST_BUDGET=4`. El gateway expone `cycle_tokens_used`, `cycle_tokens_remaining`, `cycle_budget_exhausted`, `request_attempts`, `requests_remaining` y los topes de peticiones en `/api/v1/agents/llm/status`; los contadores de ciclo se reinician en `AgentService.run_analysis` (motor autónomo y `/agents/analyze`) y no permiten eludir los topes diarios. Las peticiones cuentan cada intento, incluidos los fallidos, porque la cuota gratuita de Gemini limita llamadas (20/día/modelo).
- **Vault**: el contenedor usa `VAULT_STORAGE_PATH=/app/vault/.vault_store.json`; en el host se copió el mismo vault a `backend/.vault_store.json` para que los scripts locales usen el path por defecto. Ambos archivos siguen ignorados por Git.
- **CI**: el workflow ahora también se ejecuta en la rama `master` (antes solo `main`/`develop`, por lo que el push a `master` no disparaba comprobaciones).
- **Verificación local**: **344 tests aprobados**, cobertura 79,59 %, Ruff, formateo y MyPy sin errores, `npm run build` correcto. Oxlint mantiene avisos de hooks preexistentes.

---

*Fin del resumen de traspaso. Al iniciar el nuevo chat, comparte este archivo o pide continuar a partir de la sección 8.*

*Última actualización: 2026-10-25. Trading real activado explícitamente en Testnet (Mainnet bloqueado) y smoke test real compra/venta validado. Guard de correlación aplicado al sizing autónomo (reduce ≥0,75, bloquea ≥0,95). Detector de régimen de mercado y informe de concentración/correlación solo lectura, con paneles en la interfaz; etiquetas de régimen en el escaneo shadow y recuperación de órdenes cerradas propias dentro de una ventana configurable. Comité LLM validado con Google Gemini, presupuestos diario/ciclo y claves cargadas desde `.env`. CI extendido a `master`. Verificación local: 344 tests aprobados (cobertura 79,59 %), Ruff/MyPy/formateo y build TypeScript/Vite correctos. La migración Alembic d5e2b3c4f6a7 está aplicada a la base local.*
