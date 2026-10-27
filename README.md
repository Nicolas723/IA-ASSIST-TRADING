# AI Trading Assistant

Asistente de trading algorítmico con agentes de IA, ejecución en **Paper**, **Binance Testnet** y **Binance Mainnet** (bloqueado por defecto). Incluye comité de analistas, gestión de riesgo, OCO nativa en el exchange, guardián local de Stop Loss/Take Profit, journal en PostgreSQL, dashboard web y backups verificables.

> ⚠️ **Aviso importante**
> Este software no garantiza ganancias. El trading puede producir pérdidas, incluso con todos los controles activos. La confianza del modelo, el régimen de mercado y la correlación son medidas descriptivas, **no probabilidades de éxito**. Empieza siempre en Paper y después en Testnet. No uses dinero que no puedas permitirte perder.

---

## Índice

1. [Qué necesitas](#1-qué-necesitas)
2. [Instalación paso a paso](#2-instalación-paso-a-paso)
3. [Primer arranque](#3-primer-arranque)
4. [Configurar la IA](#4-configurar-la-ia)
5. [Conectar Binance Testnet](#5-conectar-binance-testnet)
6. [Tus primeras operaciones](#6-tus-primeras-operaciones)
7. [Operación diaria](#7-operación-diaria)
8. [Copias de seguridad](#8-copias-de-seguridad)
9. [Seguridad](#9-seguridad)
10. [Actualizar el programa](#10-actualizar-el-programa)
11. [Solución de problemas](#11-solución-de-problemas)
12. [Tests y desarrollo](#12-tests-y-desarrollo)
13. [Estructura del proyecto](#13-estructura-del-proyecto)
14. [Limitaciones conocidas](#14-limitaciones-conocidas)

---

## 1. Qué necesitas

### Software

| Herramienta | Versión | Para qué |
|---|---|---|
| **Docker Desktop** | Con Compose v2 | PostgreSQL, Redis, backend, métricas |
| **Node.js + npm** | Node 22 LTS recomendado | Dashboard web (Vite + React) |
| **Git** | Cualquiera reciente | Clonar y actualizar |
| **Python** | 3.13+ (opcional) | Tests, scripts de backup y utilidades |

### Recursos y puertos

- 4 GB de RAM libres como mínimo.
- Puertos en **loopback** (`127.0.0.1`): `8000` API, `5173` dashboard, `5432` PostgreSQL, `6379` Redis, `9090` métricas, `9091` Prometheus, `3001` Grafana.
- No abras estos puertos a Internet sin HTTPS, firewall y `API_AUTH_TOKEN`.

### Cuentas

- **Binance Testnet** (gratis): <https://testnet.binance.vision>. Crea claves API solo para Spot y **sin permiso de retirada**.
- Un proveedor de IA. La opción gratuita recomendada es **Groq** (<https://console.groq.com>). También puedes usar Anthropic, OpenAI, Google o DeepSeek.

---

## 2. Instalación paso a paso

### 2.1 Clonar el repositorio

```powershell
git clone <URL_DEL_REPOSITORIO> ai-trading-assistant
cd ai-trading-assistant
```

### 2.2 Crear el archivo de configuración

El backend necesita `backend/.env`. Créalo a partir de la plantilla:

```powershell
Copy-Item backend\.env.example backend\.env
```

En Linux/macOS:

```bash
cp backend/.env.example backend/.env
```

> Si `backend/.env` no existe antes de levantar Docker, Docker puede crear un **directorio** con ese nombre y el backend fallará. Crea el archivo primero.

### 2.3 Generar los secretos obligatorios

Edita `backend/.env` y cambia como mínimo estas dos variables:

```dotenv
APP_SECRET_KEY=<cadena larga y aleatoria>
API_KEY_ENCRYPTION_KEY=<clave Fernet>
```

Genera `APP_SECRET_KEY`:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

Genera `API_KEY_ENCRYPTION_KEY`:

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Si el segundo comando falla porque `cryptography` no está instalado en tu equipo, puedes generarlo dentro del contenedor después del primer arranque:

```powershell
docker exec ata-backend python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Copia el valor en `backend/.env` y reinicia el backend. Hazlo **antes de guardar claves de exchange** en el vault: si el vault se crea con la clave derivada de `APP_SECRET_KEY`, añadir después una clave explícita distinta lo deja ilegible.

> **Guarda `API_KEY_ENCRYPTION_KEY` en un gestor de contraseñas y fuera de este equipo.** Sin ella, las claves de exchange guardadas en el vault no se pueden descifrar. No la cambies después de guardar credenciales salvo que sepas cómo rotar el vault.

### 2.4 Levantar el stack

```powershell
.\scripts\dev-up.ps1
```

En Linux/macOS/WSL:

```bash
./scripts/dev-up.sh
```

La primera vez construye la imagen del backend y tarda varios minutos. Comprueba que los contenedores estén sanos:

```powershell
docker ps --format "table {{.Names}}\t{{.Status}}"
```

Deberías ver `ata-postgres`, `ata-redis`, `ata-backend`, `ata-prometheus` y `ata-grafana`.

### 2.5 Aplicar las migraciones de base de datos

Solo la primera vez y después de cada actualización:

```powershell
docker exec ata-backend alembic upgrade head
```

### 2.6 Comprobar el backend

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod http://127.0.0.1:8000/api/v1/status
```

La documentación interactiva de la API está en <http://127.0.0.1:8000/docs>.

### 2.7 Levantar el dashboard

Instala las dependencias la primera vez:

```powershell
npm --prefix frontend install
```

**Opción simple (uso diario):** compila la interfaz una vez y ábrela desde el
propio backend, sin procesos extra:

```powershell
npm --prefix frontend run build
```

Abre <http://localhost:8000>. Vuelve a compilar solo cuando actualices el código.

**Opción desarrollo (recarga en caliente):**

```powershell
.\scripts\dev-frontend.ps1
```

Abre <http://localhost:5173>.

La interfaz se muestra en español o inglés según el idioma del navegador; se
puede cambiar con el selector de la cabecera. Para dejarlo funcionando 24/7 en
un servidor, sigue [docs/DESPLIEGUE_VPS.md](docs/DESPLIEGUE_VPS.md).

---

## 3. Primer arranque

Antes de tocar dinero, comprueba este orden:

1. **Backend sano**: `http://127.0.0.1:8000/health` responde `{"status":"ok"}`.
2. **Base de datos migrada**: `docker exec ata-backend alembic upgrade head` termina sin errores.
3. **Interfaz abierta**: <http://localhost:8000> (o <http://localhost:5173> en modo desarrollo).
4. **Sin posiciones abiertas** al empezar.
5. **Mainnet apagado**: no marques el segundo opt-in.

Si algo falla, revisa los logs antes de continuar:

```powershell
docker logs ata-backend --tail 100
docker logs ata-postgres --tail 50
```

---

## 4. Configurar la IA

El bot puede funcionar sin proveedor LLM usando el análisis determinista, pero el comité de IA es una parte central del producto. La forma más barata de empezar es Groq.

### Opción A — Groq (gratis)

1. Crea una clave en <https://console.groq.com>.
2. Añádela a `backend/.env`:

```dotenv
GROQ_API_KEY=tu_clave_de_groq
LLM_PRIMARY_PROVIDER=groq
```

3. Reinicia el backend:

```powershell
docker restart ata-backend
```

4. Comprueba el estado:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/agents/llm/status
```

### Opción B — Otros proveedores

Añade la clave correspondiente en `backend/.env` y reinicia:

| Proveedor | Variable |
|---|---|
| Anthropic Claude | `ANTHROPIC_API_KEY` |
| OpenAI ChatGPT | `OPENAI_API_KEY` |
| Google Gemini | `GOOGLE_API_KEY` |
| DeepSeek | `DEEPSEEK_API_KEY` |
| OpenRouter | `OPENROUTER_API_KEY` |

La cadena por defecto es **Claude → ChatGPT → Google → DeepSeek → análisis determinista**. Los proveedores sin clave se omiten automáticamente. Si un proveedor devuelve `402` (sin saldo), `429` (cuota) o `401/403`, el gateway lo aparta temporalmente y continúa con el siguiente.

### Consumo de IA

En la pestaña del asistente puedes elegir:

- **Ahorro**: 1 mercado, llamadas secuenciales.
- **Normal**: hasta 3 mercados, secuencial.
- **Alto consumo**: todo el universo (máx. 10), en paralelo.

Los presupuestos son topes duros, no un objetivo de gasto. Con el plan gratuito de Groq, usa **Ahorro** o **Normal**.

---

## 5. Conectar Binance Testnet

1. Entra en <https://testnet.binance.vision> y genera una clave API.
2. En el dashboard, abre **Configuración → Conexión con Binance (Claves de API)**.
3. Pega la clave y el secreto.
4. Marca **Usar Binance Testnet**.
5. Pulsa **Guardar Claves Seguras**. Se cifran en el vault local.
6. Pulsa **Probar Conexión (Ping)** y confirma que responde.

> Las claves de exchange **no se guardan en `backend/.env`**: van al vault cifrado. Nunca actives permisos de retirada en la API.

Después, en **Configuración → Entorno, límites y protecciones**:

- **Red del exchange**: `Testnet`.
- **Máximo por orden (USDT)**: empieza bajo, por ejemplo `20` o `50`.
- **Allowlist de símbolos**: `BTC/USDT, ETH/USDT`.
- **Protección OCO nativa (Binance Spot)**: activada.
- **Permitir ejecución real autónoma**: puedes dejarlo desmarcado; al iniciar una sesión Real·Testnet, la app lo activa junto con el modo real tras la confirmación.
- **Segundo opt-in: permitir Mainnet**: **desmarcado**.
- Pulsa **Guardar límites y protecciones**.

Comprueba el estado:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/trading/real/status | Format-List
```

Debe mostrar `testnet=True`, `mainnet_allowed=False`, `credentials_configured=True` y, si hay posiciones, `oco_unprotected_positions=0`.

---

## 6. Tus primeras operaciones

### 6.1 Practicar en Paper

1. Abre la pestaña **Asistente**.
2. Elige `BTC/USDT` y `1h`.
3. En **Modo del asistente**, selecciona **Paper · practicar sin dinero real**.
4. Revisa **Ajustes de estrategia**: empieza con 1 posición y una puntuación mínima de 60–70 %.
5. Activa **El asistente ejecutará automáticamente sus señales**.
6. Pulsa **Iniciar Asistente Autónomo**.
7. Observa el estado, las señales y el registro de actividad.
8. Pulsa **Pausar Asistente** para detener nuevas entradas.

Paper simula las operaciones y no usa fondos del exchange.

### 6.2 Probar en Real · Testnet

1. Asegúrate de que las claves Testnet están guardadas y verificadas.
2. En **Modo del asistente**, selecciona **Real · usar el entorno configurado**.
3. Mantén la allowlist y el máximo por orden bajos.
4. Pulsa **Iniciar Asistente Autónomo**.
5. Acepta la confirmación de sesión, que muestra entorno, límites y protecciones.
6. Comprueba en el panel que la posición aparece protegida por OCO.
7. Pausa el asistente cuando termines la prueba.

La pausa detiene nuevas entradas, pero **no cierra ni cancela las protecciones** de las posiciones abiertas. Verifícalas por separado.

### 6.3 Cerrar una posición

Desde la interfaz puedes usar **Detener y cerrar posiciones del bot** (parada de emergencia). Cierra las posiciones gestionadas y detiene el motor; puede materializar pérdidas.

Para cerrar una posición concreta por API:

```powershell
$body = @{
    position_id = "UUID_DE_LA_POSICION"
    confirm     = $true
} | ConvertTo-Json

Invoke-RestMethod -Method Post `
    -Uri "http://127.0.0.1:8000/api/v1/trading/real/positions/close" `
    -ContentType "application/json" `
    -Body $body
```

El cierre cancela primero la OCO del símbolo y después envía una venta a mercado. No fuerces cierres manuales en el exchange sin comprobar antes que la OCO quedó cancelada, o podrías vender dos veces.

### 6.4 Mainnet (solo después de Testnet)

Mainnet está bloqueado por defecto y **todo el cambio se hace desde la interfaz**; no hace falta editar `.env` ni modificar código. El orden es:

1. **Guardar claves Mainnet**: en Configuración → Conexión con Binance, desmarca «Usar Binance Testnet», pega las claves y guarda. Guardar claves no cambia el entorno activo.
2. **Probar la conexión** con el checkbox de Testnet desmarcado. Es una prueba de lectura; no envía órdenes.
3. **Activar el segundo opt-in** en Configuración → Entorno, límites y protecciones.
4. **Seleccionar Mainnet** en «Red del exchange» y confirmar. Si hay posiciones abiertas, el cambio se bloquea.
5. **Revisar límites, allowlist y OCO** para capital real.
6. **Iniciar la sesión** en el Asistente con «Real · usar el entorno configurado» y aceptar la confirmación final, que debe mostrar `BINANCE MAINNET (dinero real)`.

Para volver a Testnet: pausa el bot, cierra o deja constancia de las posiciones, selecciona Testnet y desmarca el segundo opt-in. No actives Mainnet para “probar”. Usa Testnet hasta que el comportamiento y las protecciones estén validados. Consulta el [runbook de producción](docs/RUNBOOK_PRODUCCION.md#11-checklist-para-mainnet-no-ejecutada).

---

## 7. Operación diaria

### Antes de cada sesión

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/trading/real/status | Format-List
Invoke-RestMethod http://127.0.0.1:8000/api/v1/trading/real/positions | ConvertTo-Json -Depth 4
```

Comprueba:

- `enabled=True` y el entorno correcto (`testnet`/`mainnet`).
- `mainnet_allowed=False` mientras estés en pruebas.
- `guard_running=True`.
- `oco_unprotected_positions=0` si hay posiciones abiertas.
- Que la allowlist y el máximo por orden son los esperados.

### Durante la sesión

- Revisa el panel de estado y el registro de actividad.
- No cambies de entorno con posiciones reales abiertas: el sistema lo bloquea.
- Si el exchange o la API fallan, pausa el bot y revisa las órdenes directamente en Binance.
- No aumentes el tamaño para recuperar pérdidas.

### Para detener

| Acción | Qué hace |
|---|---|
| **Pausar Asistente** | Detiene ciclos y nuevas entradas. No cierra posiciones. |
| **Detener y cerrar posiciones del bot** | Detiene el motor e intenta cerrar posiciones gestionadas. |
| **Apagar el backend** | El guardián local deja de ejecutarse; la OCO del exchange sigue activa si existe. |

Si vas a apagar el backend con posiciones abiertas, verifica primero en Binance que la OCO está activa. Al arrancar de nuevo, el bot descubre y reasocia las OCO existentes para no duplicarlas.

---

## 8. Copias de seguridad

El backup incluye:

- Dump de PostgreSQL (`pg_dump -Fc`).
- Vault cifrado (`.vault_store.json` y overrides de runtime).
- `manifest.json` con checksums, tamaños, conteo de tablas y commit de Git.

Crea y verifica un backup:

```powershell
cd backend
python scripts/backup_runtime_data.py --verify
```

Guardar en otra carpeta y conservar 14 copias:

```powershell
python scripts/backup_runtime_data.py --output-dir D:\ata-backups --keep 14 --verify
```

Los backups quedan en `backups/` por defecto y están ignorados por Git. **Copia también `API_KEY_ENCRYPTION_KEY` por separado**: sin ella, el vault del backup no se puede descifrar.

Para programarlo en Windows, el repositorio incluye `scripts/backup-daily.ps1`, que ejecuta el backup con verificación, retiene 14 copias y escribe el resultado en `backups/backup-task.log`. Registra una tarea diaria (por ejemplo, `ATA Backup diario` a las 03:00) que ejecute ese script con el usuario actual, para que Docker Desktop sea accesible. El comando completo está en [el runbook](docs/RUNBOOK_PRODUCCION.md#53-programar-backups).

```powershell
# Comprobar la tarea (si ya la has creado)
Get-ScheduledTask -TaskName "ATA Backup diario" | Select-Object TaskName, State
Get-ScheduledTaskInfo -TaskName "ATA Backup diario" |
    Select-Object LastRunTime, LastTaskResult, NextRunTime

# Revisar el último resultado
Get-Content backups\backup-task.log -Tail 20
```

`LastTaskResult = 0` significa que el último backup terminó bien. Si el equipo está apagado a las 03:00, la tarea se ejecuta al volver a estar disponible. Mantén además una copia **off-site** en otro disco o almacenamiento cifrado.

Restaurar (con el backend detenido):

```powershell
docker stop ata-backend
cd backend
python scripts/restore_runtime_data.py --backup ..\backups\backup-XXXXXXXX-XXXXXX.zip --yes
docker start ata-backend
```

Programa el backup a diario con el Programador de tareas de Windows o `cron`. Revisa el procedimiento completo en [docs/RUNBOOK_PRODUCCION.md](docs/RUNBOOK_PRODUCCION.md).

---

## 9. Seguridad

- **Nunca** subas `backend/.env`, `backend/.vault_store.json` ni `backups/` al repositorio.
- Usa claves de exchange **sin permiso de retirada** y, si es posible, restringidas por IP.
- El stack escucha en `127.0.0.1` por defecto. No lo expongas a Internet sin HTTPS, firewall y autenticación.
- Si el proyecto se queda en tu equipo y la API sigue publicada en `127.0.0.1`, puedes dejar `API_AUTH_TOKEN` vacío. Si necesitas acceder desde otra máquina, configúralo en `backend/.env` y pon el mismo valor en `VITE_API_KEY` dentro de `frontend/.env`; todas las peticiones a `/api/*` deberán enviar `X-API-Key`.
- Rota `APP_SECRET_KEY` solo si entiendes el impacto en el vault. Para producción, fija `API_KEY_ENCRYPTION_KEY` explícitamente.
- Configura alertas (`ALERTS_ENABLED`, Telegram, email o webhook) y un heartbeat externo (`HEARTBEAT_PING_URL`) para detectar caídas.
- Revisa los logs y las órdenes del exchange periódicamente. Un proceso caído no puede avisar por sí mismo.
- Con Telegram configurado, el backend avisa si el asistente deja de completar ciclos, falla varias veces seguidas o quedó en pausa tras un reinicio. Envía un resumen diario a las `DAILY_SUMMARY_HOUR` (22 h por defecto; `ALERT_TIMEZONE` fija la zona) y responde, solo en tu chat, a `/estado`, `/resumen`, `/pausar` y `/emergencia` (cierra todo; pide `/emergencia confirmar`).
- Al llegar al límite de pérdida diaria (`MAX_DAILY_LOSS_PCT`, 2 % por defecto) el asistente deja de comprar hasta el día siguiente y avisa una vez. Las ventas y los stops siguen funcionando.

---

## 10. Actualizar el programa

```powershell
git pull
docker compose -f infra/docker/docker-compose.yml up -d --build
docker exec ata-backend alembic upgrade head
npm --prefix frontend install
```

Después:

1. Abre el dashboard y comprueba `/health`.
2. Revisa posiciones y OCO antes de reanudar el bot.
3. Ejecuta los tests si vas a desplegar cambios propios.

---

## 11. Solución de problemas

| Síntoma | Causa probable | Solución |
|---|---|---|
| El backend no arranca | `backend/.env` ausente o inválido | Créalo desde `.env.example` y revisa `docker logs ata-backend` |
| `backend/.env` es un directorio | Docker lo creó al no existir | Borra el directorio, crea el archivo y reinicia |
| PostgreSQL no está sano | Volumen antiguo con otra contraseña | Usa la contraseña de desarrollo del compose o recrea el volumen (destructivo) |
| `401` en `/api/*` | `API_AUTH_TOKEN` activo sin `VITE_API_KEY` | Configura la misma clave en el frontend o vacía el token en local |
| El dashboard no conecta | Backend caído o puerto ocupado | Comprueba `http://127.0.0.1:8000/health` y el proxy de Vite |
| La IA no responde | Sin claves, cuota agotada o `402` | Revisa `/api/v1/agents/llm/status`; usa Groq o espera el cooldown |
| `OCO unprotected` | OCO no creada o descubrimiento fallido | Ejecuta `POST /api/v1/trading/real/oco/ensure` y revisa los logs; no dejes la posición sin proteger |
| `No free BTC balance` | Ya hay una venta/OCO en curso | Revisa órdenes abiertas en Binance antes de reintentar |
| El bot no abre operaciones | Motor pausado, auto-ejecución apagada, allowlist o Risk Guard | Revisa estado, configuración y registro de actividad |

Si hay un incidente con posiciones abiertas, prioriza: **pausar → verificar en el exchange → proteger o cerrar → investigar**.

---

## 12. Tests y desarrollo

Backend:

```powershell
cd backend
pip install -e ".[dev]"
pytest
ruff check src tests
ruff format --check src tests
mypy src
```

Frontend:

```powershell
npm --prefix frontend install
npm --prefix frontend run build
npm --prefix frontend run lint
```

La suite de backend exige una cobertura mínima del 70 %. Los tests de integración pueden necesitar PostgreSQL y Redis levantados.

### Smoke test de Testnet

Con el backend en marcha:

```powershell
python scripts/real_testnet_smoke.py
```

Sin `--execute` solo comprueba el estado y las posiciones. Para enviar una orden real de Testnet:

```powershell
python scripts/real_testnet_smoke.py --execute --yes --quantity 0.0001
```

Para abrir y cerrar en el mismo ensayo:

```powershell
python scripts/real_testnet_smoke.py --execute --yes --quantity 0.0001 --close
```

El script se niega a operar en Mainnet salvo que pases `--allow-mainnet`. No sustituye la verificación manual en la interfaz de Binance.

---

## 13. Estructura del proyecto

```text
ai-trading-assistant/
├── backend/
│   ├── src/
│   │   ├── domain/              # Entidades y puertos
│   │   ├── application/         # Servicios: trading, riesgo, agentes, OCO
│   │   ├── infrastructure/      # PostgreSQL, exchange, LLM, vault
│   │   ├── interfaces/          # API REST y WebSocket
│   │   └── shared/              # Configuración y utilidades
│   ├── scripts/                 # Backup, restore y smoke test
│   └── .env.example
├── frontend/                    # Dashboard React + Vite
├── infra/docker/                # Docker Compose y configuración
├── scripts/                     # Arranque portable (Windows/Linux)
├── docs/
│   ├── RUNBOOK_PRODUCCION.md    # Operación, backups, incidentes
│   └── GUIA_USUARIO_TRADING_AUTONOMO.md
├── backups/                     # Copias locales (ignoradas por Git)
└── PROJECT_OVERVIEW.md          # Arquitectura y estado técnico
```

Documentación adicional:

- [Runbook de producción](docs/RUNBOOK_PRODUCCION.md)
- [Guía del trading autónomo](docs/GUIA_USUARIO_TRADING_AUTONOMO.md)
- [Visión general del proyecto](PROJECT_OVERVIEW.md)
- API interactiva: <http://127.0.0.1:8000/docs>

---

## 14. Limitaciones conocidas

- **Solo Binance Spot** para ejecución real. La ejecución es long-only: no hay cortos.
- **Una OCO por símbolo**. El stop es un `STOP_LOSS_LIMIT`; en un gap fuerte puede no ejecutarse al precio previsto.
- **El guardián local no sustituye al exchange**: si el backend se cae, la OCO sigue protegiendo la posición, pero el guardián local no.
- **Testnet no reproduce el mercado real**: liquidez, spreads, slippage y ejecución difieren.
- **La IA no se reentrena** con las operaciones de Testnet. Los resultados pasados no predicen resultados futuros.
- **El modo Mainnet está bloqueado por configuración** hasta completar el checklist y activar los dos opt-ins.
- **No hay garantía de rentabilidad** ni de pérdida máxima: comisiones, slippage, gaps y fallos técnicos pueden aumentar las pérdidas.

Si vas a operar con dinero real, lee el runbook completo y empieza con un límite por orden muy bajo.
