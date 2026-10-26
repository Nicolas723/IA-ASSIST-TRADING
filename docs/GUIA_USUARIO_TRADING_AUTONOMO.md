# Guía de uso: trading autónomo con IA

Esta guía explica cómo usar el asistente desde el dashboard. La IA analiza el mercado y puede enviar órdenes por su cuenta si se habilita la ejecución automática. **No garantiza ganancias**: el trading puede causar pérdidas, incluso en Testnet las condiciones no representan exactamente el mercado real.

## Configurar los proveedores de IA

El comité usa una cadena de proveedores LLM en este orden: **Claude → ChatGPT → Google → DeepSeek → análisis determinista**. Prueba el siguiente solo si el anterior no responde o devuelve una respuesta inválida. Solo se intentan los proveedores que tengan una clave configurada: si una clave está vacía, ese proveedor se omite y la cadena continúa, así que puedes añadir credenciales de una en una sin romper el bot. Si un proveedor devuelve cuota agotada (`429`), saldo insuficiente (`402`) o credenciales rechazadas (`401/403`), el gateway lo aparta temporalmente (`provider_cooldowns` en `/api/v1/agents/llm/status`) y no gasta peticiones en él hasta que expire el plazo; el `retryDelay` de Gemini se respeta cuando viene informado. Ollama puede añadirse como proveedor local activando `OLLAMA_ENABLED=true` y añadiendo `ollama` a `LLM_FALLBACK_PROVIDERS`.

Si quieres una opción **completamente gratuita y permanente** para las pruebas, usa Groq (`GROQ_API_KEY`): plan sin tarjeta con 30 peticiones/min, 1.000 peticiones/día, 8.000 tokens/min y 200.000 tokens/día por modelo. Es suficiente para los modos Ahorro y Normal, pero no para Alto consumo en paralelo, que agotaría el límite por minuto. Cerebras ya no tiene tier gratuito permanente (solo 5 $ de prueba durante 30 días) y los modelos `:free` de OpenRouter están limitados a 50 peticiones/día sin comprar crédito.

1. Añade en `backend/.env` las claves de los proveedores que quieras usar (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GOOGLE_API_KEY`, `DEEPSEEK_API_KEY`, `GROQ_API_KEY`, `OPENROUTER_API_KEY`). No compartas esas claves en el chat ni en capturas.
2. La cadena ya viene configurada (`LLM_PRIMARY_PROVIDER=anthropic` y `LLM_FALLBACK_PROVIDERS=["openai","google","deepseek"]`). Solo cambia esos valores si quieres otro orden. Para Google, el modelo se elige con `GOOGLE_DEFAULT_MODEL` (el prefijo es `GOOGLE_`, no `GEMINI_`); para Claude, con `ANTHROPIC_DEFAULT_MODEL`; para ChatGPT, con `OPENAI_DEFAULT_MODEL`; para DeepSeek usa `DEEPSEEK_DEFAULT_MODEL` (`deepseek-chat` o `deepseek-reasoner`); para Groq, `GROQ_DEFAULT_MODEL` (`openai/gpt-oss-120b` por defecto). Para usar Groq como primario: `LLM_PRIMARY_PROVIDER=groq` y añade el resto a `LLM_FALLBACK_PROVIDERS`.
3. Reinicia el backend para que cargue las claves y el modelo.
4. Consulta `GET /api/v1/agents/llm/status` para ver qué proveedores están configurados y `POST /api/v1/agents/llm/health` para comprobar conectividad. Esas respuestas nunca muestran claves.

Por ciclo, los agentes técnico y de acción del precio generan una solicitud cada uno. El proveedor alternativo solo se usa cuando el anterior falla; cada proveedor puede cobrar sus propias tarifas. El Risk Manager y los límites de ejecución siguen siendo controles deterministas independientes. La cadena LLM **no se reentrena con las operaciones de Testnet**: para aprendizaje real hacen falta experimentos y evaluación aparte.

`GET /api/v1/agents/llm/status` desglosa tokens, llamadas, fallos, latencia media y coste estimado por agente y por proveedor. El coste solo se calcula si configuras `LLM_INPUT_COST_PER_MILLION_USD` y `LLM_OUTPUT_COST_PER_MILLION_USD` con las tarifas de tu modelo; si ambas quedan en `0`, la respuesta devuelve `null` en lugar de presentar un coste falso de cero. Los contadores son en memoria y se reinician a medianoche UTC o al reiniciar el backend.

Puedes acotar el gasto con `LLM_DAILY_TOKEN_BUDGET` y `LLM_CYCLE_TOKEN_BUDGET` y el número de llamadas con `LLM_DAILY_REQUEST_BUDGET` y `LLM_CYCLE_REQUEST_BUDGET` (0 = sin límite). Los topes de ciclo se reinician al empezar cada análisis, incluido el botón de análisis manual de la API (`POST /api/v1/agents/analyze`), y los diarios solo reinician a medianoche UTC o al reiniciar el backend; empezar un ciclo nuevo nunca permite saltarse el tope diario. Las peticiones cuentan cada intento contra el proveedor, incluidos los fallidos, porque las cuotas gratuitas limitan llamadas. Los proveedores en cooldown no consumen intentos. Cuando se agota un tope, el comité cae a su análisis determinista y lo registra como fallback. Configuración actual: 200 000 tokens/día, 12 000 tokens/ciclo, 18 peticiones/día y 8 peticiones/ciclo (dos especialistas × cuatro proveedores).

Validación operativa del 2026-10-24/25: Google Gemini (`gemini-3.8-flash`, SDK `google-genai`) respondió con llamadas reales en técnico y acción del precio. La cuenta de DeepSeek devolvió `402 Insufficient Balance` (clave válida, sin saldo): el gateway lo aparca 30 minutos hasta que se recargue. OpenAI estaba configurado pero su cuenta devolvía `insufficient_quota`. Claude y ChatGPT aún no tienen clave: sus adaptadores quedan registrados automáticamente en cuanto se pegue `ANTHROPIC_API_KEY` o `OPENAI_API_KEY` en `.env` y se reinicie el backend. La cuota gratuita de Gemini es de 20 peticiones al día por modelo y se reinicia a medianoche del Pacífico (≈07:00–08:00 UTC), no a medianoche UTC. Para no agotarla en una hora, el bot ejecuta el comité completo como máximo cada `llm_analysis_interval_seconds` (30 min por defecto) mientras sigue gestionando salidas y SL/TP en cada ciclo corto. Si ningún proveedor responde, el comité usa el análisis determinista. Un proveedor configurado sin créditos no es un proveedor operativo.

## Modo de consumo de IA

En la pestaña del asistente, el selector **Consumo de IA** cambia cuántos mercados analiza el comité y cuánto gasta en cada análisis:

| Modo | Mercados por análisis | Ejecución | Peticiones/ciclo | Tokens/ciclo | Peticiones/día | Tokens/día |
| --- | --- | --- | --- | --- | --- | --- |
| Ahorro | 1 (el mejor del pre-escaneo) | Secuencial | 8 | 12 000 | 18 | 200 000 |
| Normal | Hasta 3 | Secuencial | 24 | 40 000 | 72 | 600 000 |
| Alto consumo | Todo el universo (máx. 10) | Paralela | 80 | 120 000 | 300 | 2 000 000 |

El cambio se aplica en el **siguiente análisis**, sin reiniciar el bot ni el backend, y se guarda en `vault/.runtime_llm_config.json` (separado de la configuración de trading). La captura de velas y de contexto de investigación sigue siendo secuencial porque comparte una sola sesión de base de datos; solo las llamadas al comité corren en paralelo. En modo alto, las decisiones se evalúan y ejecutan en orden de ranking, y `max_open_positions` sigue siendo la última barrera. Los presupuestos son **topes duros, no un gasto objetivo**: el coste real depende de tu proveedor y modelo, y las tarifas solo se estiman si configuras `LLM_INPUT_COST_PER_MILLION_USD` y `LLM_OUTPUT_COST_PER_MILLION_USD`. Con el plan gratuito de Groq, usa Ahorro o Normal; el modo alto satura el límite de 8.000 tokens/minuto.

## Escaneo multimercado del bot autónomo

El bot puede vigilar varios mercados a la vez sin multiplicar las llamadas al LLM. Al iniciar la sesión resuelve un universo con `scan_symbols` (lista explícita en `POST /api/v1/autonomous/start`) o, si se omite, con `SHADOW_SCAN_SYMBOLS`. El símbolo principal siempre se incluye y el máximo es 10 mercados. Ejemplo de inicio con universo explícito:

```json
{
  "symbol": "BTC/USDT",
  "scan_symbols": ["BTC/USDT", "ETH/USDT", "BNB/USDT", "SOL/USDT", "XRP/USDT"],
  "timeframe": "1h",
  "interval_seconds": 300,
  "llm_analysis_interval_seconds": 1800,
  "execution_mode": "real",
  "confirm_real_trading": true
}
```

En cada ciclo el motor consulta velas cerradas de todos los mercados de forma secuencial, calcula el retorno reciente y el régimen descriptivo, y los ordena con un ajuste pequeño de tendencia (±0,5 puntos). Después envía al comité los mejores candidatos **permitidos por `REAL_TRADING_ALLOWED_SYMBOLS`** según el modo de consumo elegido: 1 en Ahorro, hasta 3 en Normal o todo el universo en Alto consumo; el resto queda medido en los logs. La puntuación es un criterio para elegir qué mercado analizar, no un retorno esperado, una probabilidad de éxito ni una recomendación. El comité y el Risk Guard siguen decidiendo si se opera.

El seguimiento de SL/TP revisa **todas** las posiciones abiertas con el precio de su propio símbolo, aunque el comité esté analizando otro mercado. En modo real, un mercado fuera de la allowlist se sigue escaneando y sus posiciones se gestionan, pero no se abre una entrada nueva en él. La allowlist se cambia en Configuración > Control de ejecución; ampliarla no requiere reiniciar el motor, pero sí iniciar una sesión nueva para que el universo se resuelva con los cambios.

## Escaneo de mercado en sombra

`POST /api/v1/agents/scan/shadow` compara hasta cinco símbolos por solicitud sin crear órdenes ni llamar a los modelos LLM. Ejemplo:

```json
{
  "symbols": ["BTC/USDT", "ETH/USDT", "SOL/USDT"],
  "timeframe": "1h",
  "lookback_candles": 24
}
```

El `activity_score` solo ordena por movimiento absoluto de precio durante la ventana; no es rentabilidad esperada ni probabilidad de éxito. El cálculo utiliza velas completamente cerradas y excluye la vela en curso. El resultado es una observación en sombra, no una recomendación de compra/venta. Esta primera versión usa los datos de mercado Binance ya configurados.

El scanner incluye `quoted_spread_pct` de un order book superficial reciente y `observed_movement_less_spread_pct`, que resta el spread actual al movimiento absoluto pasado solo como contexto. No afecta el ranking ni estima retorno futuro; no incluye comisiones ni slippage. Si no hay cotización válida/fresca, `spread_status` lo indica y el sistema no trata el coste faltante como cero.

Para acumular datos de forma automática en desarrollo, configura `SHADOW_SCAN_AUTORUN=true`. Solo se permite con `APP_ENV=development`; la lista está limitada a cinco símbolos y el intervalo a un mínimo de 3600 segundos. Cada ciclo persiste el escaneo `1h` y evalúa una vela madura de escaneos anteriores. El flag viene apagado por defecto, y el worker se cancela al apagar la aplicación. No envía órdenes ni activa trading real.

Cada resultado se conserva para auditoría. Consulta las últimas ejecuciones con `GET /api/v1/agents/scan/shadow/history?limit=20`. La primera vez hay que aplicar la migración Alembic disponible en el backend; no actives trading por el resultado del escaneo.

Para medir movimiento posterior cuando ya maduró un horizonte, usa `POST /api/v1/agents/scan/shadow/{scan_id}/evaluate?horizon_candles=24` (hasta 168 velas), o `POST /api/v1/agents/scan/shadow/{scan_id}/evaluate-horizons?horizons_candles=1&horizons_candles=6&horizons_candles=24` para solicitar varias temporalidades secuencialmente. Cada snapshot se evalúa al madurar sin exigir 30 muestras; antes del vencimiento devuelve `pending`. Cuando hay datos compara descriptivamente rank 1 con alternativas y con la referencia equiponderada. Recupera resultados con `/evaluations` y agregados con `/evaluations/summary`. Los retornos son brutos y no prueban rentabilidad.

La evaluación cuenta el horizonte desde el cierre de la última vela de entrada (`evaluation_anchor_at`). El agregado separa comparaciones brutas de ventanas no solapadas; el umbral de 30 clasifica solo la evidencia estadística agregada y **no bloquea rankings ni evaluaciones shadow continuas** (`blocks_current_shadow_scan=false`). Ventanas no solapadas reducen repetición mecánica, pero no prueban independencia estadística. Este umbral tampoco es una autorización para trading real.

## Régimen de mercado (solo lectura)

`GET /api/v1/market/{symbol}/regime?timeframe=1h&limit=200` resume el comportamiento reciente de un mercado con dos etiquetas: tendencia (`uptrend`, `downtrend` o `range`) y volatilidad (`low`, `normal` o `high`). Se calcula con EMA20/EMA50, la pendiente de la EMA rápida y el percentil del ATR14.

- Solo usa velas **cerradas**; la vela en curso se excluye y se contabiliza.
- Si hay pocas velas devuelve `status=insufficient_data` en lugar de inventar una etiqueta.
- Marca `data_stale=true` cuando la última vela cerrada es más antigua de lo esperado para esa temporalidad.
- Devuelve `descriptive_only=true`, `affects_trading=false` y `execution_enabled=false`.

El panel **Régimen de Mercado** de la vista principal muestra esas métricas y motivos. Es contexto, no una señal de compra/venta: no cambia el comité, el sizing, el veto de riesgo ni la ejecución del bot. Una racha corta o un cambio brusco de volatilidad pueden hacer que la etiqueta quede obsoleta de inmediato.

Cada candidato del escaneo shadow incluye también `regime_status`, `trend_regime` y `volatility_regime`, calculados sobre las mismas velas cerradas. Es una etiqueta de contexto para comparar observaciones; el movimiento y el ranking siguen midiéndose solo sobre la ventana solicitada y la etiqueta no habilita ejecución.

## Concentración y correlación (solo lectura)

`GET /api/v1/risk/concentration?symbols=BTC/USDT,ETH/USDT&timeframe=1h&lookback=200` responde a dos preguntas: cuánto capital hay concentrado en cada activo y cómo se mueven entre sí los retornos recientes del universo consultado.

- La exposición por activo se compara con `MAX_POSITION_SIZE_PCT` y la exposición bruta con `MAX_PORTFOLIO_EXPOSURE_PCT`. Esos dos límites **sí** se aplican en el preflight de órdenes.
- La correlación de Pearson se calcula alineando velas cerradas por timestamp. Necesita al menos 30 retornos comunes; si no los hay, devuelve `null` y lo explica en `warnings`.
- El umbral de correlación alta es 0.8 y solo genera una advertencia cuando ambos activos tienen exposición.
- Devuelve `read_only=true` y `affects_trading=false`: no bloquea, no redimensiona y no envía órdenes.

El panel de riesgo muestra el desglose por activo, la correlación más alta y las advertencias. Con un solo símbolo permitido, la correlación no aporta diversificación; es útil cuando se comparan varios activos o antes de ampliar la allowlist.

## 1. Empezar sin arriesgar dinero

1. Inicia la aplicación y abre la pestaña **Asistente**.
2. Selecciona un par y un marco temporal. Empieza con `BTC/USDT` y `1h`.
3. Deja **Modo Paper** seleccionado. Paper simula las operaciones y no usa fondos del exchange.
4. Usa el perfil **Conservador (1%)**, una posición máxima y confianza mínima de 60–70%.
5. Activa **Ejecutar automáticamente** y pulsa **Iniciar Asistente Autónomo**.
6. Revisa el panel de estado, las señales y el registro de actividad. Pulsa **Pausar Asistente** para detener nuevas operaciones.

El panel **Oportunidades en Sombra y Coste de IA** es de solo lectura: muestra el ranking descriptivo del último escaneo, la decisión de la política (abstenerse o revisión manual), el uso de tokens y las notas de investigación vigentes. Ninguno de esos elementos envía órdenes ni autoriza una entrada; la etiqueta «sin ejecución» es literal.

## Memoria de investigación y datos de mercado

Las notas de investigación se guardan con procedencia, ámbito y estado de validación. Por defecto **no** se envían a los modelos. Si activas `RESEARCH_CONTEXT_IN_PROMPTS=true`, los agentes reciben solo notas `supported` y vigentes, dentro de un bloque explícitamente marcado como datos no confiables y con límites de número y caracteres (`RESEARCH_CONTEXT_MAX_NOTES`, `RESEARCH_CONTEXT_MAX_CHARS`). Esas notas no pueden autorizar una operación ni sustituir el veto de riesgo o el esquema JSON de respuesta.

La ingesta pública de velas puede usar Binance o Bybit con `MARKET_DATA_EXCHANGE`. Bybit es solo una fuente de datos: no hay ejecución, conciliación de posiciones ni OCO en ese exchange, y el panel de cobertura lo indica con `execution_available=false`.

El porcentaje de riesgo configura el riesgo teórico calculado hasta el Stop Loss, no el porcentaje de saldo usado como valor nominal de la posición. El tamaño se limita además por el perfil de riesgo y los límites de orden: asignación por activo (posición existente + orden nueva) y exposición bruta total de la cartera. Risk Guard realiza un preflight independiente y el veto del Risk Manager impide la entrada. No se garantiza la pérdida máxima por gaps, slippage, comisiones ni fallos de ejecución. El bot usa Stop Loss de 2% y objetivo de 4% (RR 2:1) por defecto.

Antes de dimensionar una compra, el bot compara el movimiento reciente del candidato con el de las demás posiciones abiertas. Si la correlación positiva es alta reduce el riesgo; si es muy alta, omite la entrada. Se configura con `CORRELATION_GUARD_ENABLED` (activado por defecto), `CORRELATION_REDUCE_THRESHOLD=0.75`, `CORRELATION_BLOCK_THRESHOLD=0.95`, `CORRELATION_MIN_SAMPLES=30` y `CORRELATION_LOOKBACK=200`. La correlación es una medida descriptiva de retornos pasados, no una probabilidad de pérdida ni una previsión, y no sustituye al resto de límites. Con una sola posición abierta no se aplica.

## 2. Probar con Binance Testnet

1. En **Configuración → Conexión con Binance**, elige **Testnet**, introduce las claves API de Testnet y guarda/probar conexión.
2. En **Configuración → Modo de ejecución y entorno**, confirma que el entorno sea **Testnet**. No actives el opt-in Mainnet.
3. En **Asistente**, elige **Real · Testnet**, revisa el par y, si lo necesitas, ajusta los parámetros en **Ajustes de estrategia**.
4. Asegúrate de que la ejecución automática esté activada e inicia. La única confirmación de la sesión muestra entorno, límites y protecciones; al aceptarla, la app habilita el modo real y el bot.
6. Comprueba las órdenes y posiciones desde el panel. Para detener nuevas entradas, pulsa **Pausar**. La pausa no cierra posiciones abiertas; verifica y gestiona sus protecciones por separado.

El modo Real continúa sujeto a la allowlist, máximo por orden, Risk Guard y confirmación de la sesión autónoma. Las confirmaciones manuales normales de órdenes siguen siendo obligatorias en el trading manual; la sesión autónoma es una autorización explícita para que el bot ejecute las señales durante esa sesión.

Antes de cada entrada nueva en Real, el bot comprueba que el exchange responde a una lectura de saldo. Si esa comprobación falla, la entrada se suspende y verás un aviso en el registro; las ventas y los cierres por Stop Loss/Take Profit no se bloquean. Esto evita abrir riesgo nuevo mientras el registro local puede estar desincronizado con el exchange.

## 3. Mainnet: dinero real (no recomendado para empezar)

Mainnet debe ser el último paso, solo después de comprender y validar el comportamiento en Paper y Testnet. Las claves Mainnet se guardan separadas de Testnet.

1. Guarda y prueba claves de **Mainnet** con permisos mínimos necesarios. Nunca habilites retiros en la API.
2. Abre **Configuración → Entorno, límites y protecciones**, selecciona **Mainnet** y marca **Segundo opt-in: permitir Mainnet**. Es una autorización separada que permanece guardada.
4. Mantén una allowlist pequeña y un límite por orden bajo; conserva la confirmación manual.
5. En el Asistente selecciona **Real · Mainnet**. La única confirmación antes de la sesión muestra el resumen completo; acéptala solo si aceptas el riesgo.

Cambiar a Mainnet no activa por sí mismo el trading real. Se requieren credenciales Mainnet, el opt-in Mainnet, modo Real y una confirmación adicional al iniciar la sesión autónoma. El sistema bloquea cambios de entorno con posiciones reales abiertas.

## 4. Qué hacen los controles

- **Paper / Real**: simulación local frente a ejecución en el exchange del entorno seleccionado.
- **Testnet / Mainnet**: entorno de prueba frente a dinero real; no intercambia ni sobrescribe las claves.
- **Confianza mínima**: filtra señales de menor confianza; no es una probabilidad de beneficio garantizada.
- **Tamaño por operación**: porcentaje del saldo asignado a la entrada. Empieza en 1%.
- **Máximo de posiciones**: limita posiciones simultáneas del bot.
- **Allowlist y máximo por orden**: salvaguardas globales para órdenes reales, configuradas en Configuración.
- **Pausar**: detiene ciclos y nuevas órdenes; no liquida posiciones existentes.
- **Parada de emergencia**: intenta detener el bot y cerrar posiciones administradas; úsala solo entendiendo que puede materializar pérdidas y no sustituye la verificación directa en el exchange.
- **MLOps**: muestra resultados atribuidos a decisiones del comité cuando se dispone de datos suficientes; es analítica, no autoentrenamiento ni garantía de mejora.
- **Régimen de mercado**: etiqueta descriptiva de tendencia y volatilidad; no es una señal, no autoriza entradas y no modifica el riesgo ni la ejecución.

## 5. Hábitos recomendados

- Mantén el backend conectado y revisa periódicamente logs, órdenes, posiciones y saldo en el exchange.
- No uses claves con permisos de retiro ni compartas claves o secretos en capturas, logs o soporte.
- No subas el tamaño por operación para recuperar pérdidas. Si no entiendes un control, permanece en Paper.
- Antes de reiniciar o actualizar, revisa posiciones y órdenes abiertas directamente en el exchange.
- Para incidencias, detén el bot primero; pausar nuevas decisiones no cancela automáticamente las órdenes protectoras del exchange.
