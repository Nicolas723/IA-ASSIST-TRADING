# Guía de uso: trading autónomo con IA

Esta guía explica cómo usar el asistente desde el dashboard. La IA analiza el mercado y puede enviar órdenes por su cuenta si se habilita la ejecución automática. **No garantiza ganancias**: el trading puede causar pérdidas, incluso en Testnet las condiciones no representan exactamente el mercado real.

## Configurar los proveedores de IA

El comité puede usar OpenAI como proveedor principal y probar Gemini, Anthropic y OpenRouter en ese orden si OpenAI no responde o devuelve una respuesta inválida. Solo se intentan los proveedores que tengan una clave configurada. Ollama puede añadirse como proveedor local activando `OLLAMA_ENABLED=true` y añadiendo `ollama` a `LLM_FALLBACK_PROVIDERS`.

1. Añade en `backend/.env` las claves de los proveedores que quieras usar (`OPENAI_API_KEY`, `GOOGLE_API_KEY`, `ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY`). No compartas esas claves en el chat ni en capturas.
2. Configura `LLM_PRIMARY_PROVIDER=openai` y la cadena `LLM_FALLBACK_PROVIDERS=["google","anthropic","openrouter"]` según tus cuentas y preferencias.
3. Reinicia el backend para que cargue las claves.
4. Consulta `GET /api/v1/agents/llm/status` para ver qué proveedores están configurados y `POST /api/v1/agents/llm/health` para comprobar conectividad. Esas respuestas nunca muestran claves.

Por ciclo, los agentes técnico y de acción del precio generan una solicitud cada uno. El proveedor alternativo solo se usa cuando el anterior falla; cada proveedor puede cobrar sus propias tarifas. El Risk Manager y los límites de ejecución siguen siendo controles deterministas independientes. La cadena LLM **no se reentrena con las operaciones de Testnet**: para aprendizaje real hacen falta experimentos y evaluación aparte.

`GET /api/v1/agents/llm/status` desglosa tokens, llamadas, fallos, latencia media y coste estimado por agente y por proveedor. El coste solo se calcula si configuras `LLM_INPUT_COST_PER_MILLION_USD` y `LLM_OUTPUT_COST_PER_MILLION_USD` con las tarifas de tu modelo; si ambas quedan en `0`, la respuesta devuelve `null` en lugar de presentar un coste falso de cero. Los contadores son en memoria y se reinician a medianoche UTC o al reiniciar el backend.

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

## 2. Probar con Binance Testnet

1. En **Configuración → Conexión con Binance**, elige **Testnet**, introduce las claves API de Testnet y guarda/probar conexión.
2. En **Configuración → Modo de ejecución y entorno**, confirma que el entorno sea **Testnet**. No actives el opt-in Mainnet.
3. En **Asistente**, elige **Real · Testnet**, revisa el par y, si lo necesitas, ajusta los parámetros en **Ajustes de estrategia**.
4. Asegúrate de que la ejecución automática esté activada e inicia. La única confirmación de la sesión muestra entorno, límites y protecciones; al aceptarla, la app habilita el modo real y el bot.
6. Comprueba las órdenes y posiciones desde el panel. Para detener nuevas entradas, pulsa **Pausar**. La pausa no cierra posiciones abiertas; verifica y gestiona sus protecciones por separado.

El modo Real continúa sujeto a la allowlist, máximo por orden, Risk Guard y confirmación de la sesión autónoma. Las confirmaciones manuales normales de órdenes siguen siendo obligatorias en el trading manual; la sesión autónoma es una autorización explícita para que el bot ejecute las señales durante esa sesión.

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

## 5. Hábitos recomendados

- Mantén el backend conectado y revisa periódicamente logs, órdenes, posiciones y saldo en el exchange.
- No uses claves con permisos de retiro ni compartas claves o secretos en capturas, logs o soporte.
- No subas el tamaño por operación para recuperar pérdidas. Si no entiendes un control, permanece en Paper.
- Antes de reiniciar o actualizar, revisa posiciones y órdenes abiertas directamente en el exchange.
- Para incidencias, detén el bot primero; pausar nuevas decisiones no cancela automáticamente las órdenes protectoras del exchange.
