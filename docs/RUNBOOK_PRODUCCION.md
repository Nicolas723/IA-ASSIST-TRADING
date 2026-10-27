# Runbook de producción — AI Trading Assistant

> **Estado actual:** trading real habilitado **solo en Binance Testnet**. Mainnet
> está bloqueado (`ALLOW_MAINNET_TRADING=false`, `trading_environment=testnet`).
> Este documento no autoriza el paso a Mainnet: ese paso exige la checklist de la
> sección 11 y una decisión humana explícita.
>
> **Aviso:** el bot no garantiza rentabilidad. Confianza, régimen de mercado y
> correlación son medidas descriptivas, no probabilidades de ganar.

---

## 1. Principios operativos

1. **El exchange es la fuente de verdad.** El ledger local (posiciones, órdenes,
   trades) es best-effort y se rehidrata desde PostgreSQL al arrancar.
2. **Dos capas de protección.** La OCO de Binance protege aunque el backend esté
   apagado; el guardián local solo protege mientras el proceso esté vivo. Si la
   OCO falla, el guardián local sigue activo.
3. **Nunca se abre riesgo nuevo con el ledger desincronizado.** El preflight de
   conciliación bloquea entradas si no puede leer el exchange; las salidas no se
   bloquean.
4. **Los secretos no se versionan.** `.env`, el vault y los backups viven fuera
   de Git. `backend/.env.example` no contiene claves reales.
5. **Un cambio de entorno no es automático.** El bot nunca pasa de Testnet a
   Mainnet por sí solo.

---

## 2. Servicios y puertos

| Servicio | Contenedor | Puerto por defecto | Bind por defecto |
|---|---|---|---|
| Backend FastAPI | `ata-backend` | 8000 | `127.0.0.1` |
| Métricas | `ata-backend` | 9090 | `127.0.0.1` |
| PostgreSQL/TimescaleDB | `ata-postgres` | 5432 | `127.0.0.1` |
| Redis | `ata-redis` | 6379 | `127.0.0.1` |
| Prometheus | `ata-prometheus` | 9091 | `127.0.0.1` |
| Grafana | `ata-grafana` | 3001 | `127.0.0.1` |

Todos los puertos se publican en loopback por defecto. Para exponer el backend
en una LAN o VPS:

1. Configura `API_AUTH_TOKEN` (sección 7).
2. Pon el backend detrás de un reverse proxy con TLS (Caddy, Nginx, Traefik).
3. Solo entonces define `BACKEND_BIND_ADDRESS=0.0.0.0` en el entorno del compose.

No expongas PostgreSQL ni Redis a Internet.

---

## 3. Preflight antes de cada sesión

Desde la raíz del repositorio:

```powershell
docker compose -f infra/docker/docker-compose.yml ps
curl.exe http://127.0.0.1:8000/health
curl.exe http://127.0.0.1:8000/api/v1/trading/real/status
curl.exe http://127.0.0.1:8000/api/v1/alerts/status
```

Si `API_AUTH_TOKEN` está definido, añade `-H "X-API-Key: <token>"` a las
llamadas a `/api/*`.

Comprueba en `/real/status`:

| Campo | Valor esperado en Testnet |
|---|---|
| `execution_mode` | `real` |
| `testnet` | `true` |
| `mainnet_allowed` | `false` |
| `guard_running` | `true` |
| `credentials_configured` | `true` |
| `protection_mode` | `exchange_oco` si hay OCO; `local_guard` si no |
| `oco_protected_positions` | igual al número de posiciones Binance abiertas |
| `oco_unprotected_positions` | `0` (si es > 0, ejecutar la sección 6) |
| `oco_discovery_failed_exchanges` | `[]` |

Comprueba también que el motor autónomo está en el estado deseado
(`/api/v1/autonomous/status`): el bot **no** arranca solo.

---

## 4. Variables de entorno críticas

Archivo real: `backend/.env` (ignorado por Git). Plantilla:
`backend/.env.example`.

| Variable | Uso | Nota |
|---|---|---|
| `APP_SECRET_KEY` | Firma interna / derivación legacy | Debe ser aleatoria y larga |
| `API_KEY_ENCRYPTION_KEY` | Clave Fernet del vault | **Obligatoria en producción**; si se pierde, las claves del vault no se pueden descifrar |
| `API_AUTH_TOKEN` | Exige `X-API-Key` en `/api/*` | Vacío = sin auth (solo aceptable en loopback) |
| `HEARTBEAT_PING_URL` | Dead-man's switch externo | Vacío = deshabilitado |
| `HEARTBEAT_INTERVAL_SECONDS` | Frecuencia del ping | 60 s por defecto, mínimo 15 |
| `EXECUTION_MODE` / `ENABLE_REAL_TRADING` | Habilitan ejecución real | El runtime config de la UI manda sobre `.env` |
| `ALLOW_MAINNET_TRADING` | Segundo opt-in para dinero real | Debe seguir en `false` |
| `REAL_TRADING_OCO_ENABLED` | OCO nativa de Binance | Activar solo tras validar en Testnet |
| `REAL_TRADING_MAX_NOTIONAL` | Límite por operación | Última barrera del servicio live |
| `REAL_TRADING_ALLOWED_SYMBOLS` | Allowlist de ejecución | Los símbolos fuera se escanean pero no se operan |

Reglas:

- `API_KEY_ENCRYPTION_KEY` se fija **antes** de guardar credenciales y se
  respalda en un gestor de contraseñas. Cambiarla invalida el vault.
- Rotar `APP_SECRET_KEY` es seguro solo si `API_KEY_ENCRYPTION_KEY` está fijada
  explícitamente; si está vacía, la clave del vault se deriva de
  `APP_SECRET_KEY` y la rotación rompe el descifrado.
- Guarda una copia de `API_KEY_ENCRYPTION_KEY` **fuera** del servidor. Un backup
  del vault sin esa clave es ilegible.

---

## 5. Backups y restauración

### 5.1 Qué se guarda

`backend/scripts/backup_runtime_data.py` crea un zip en `backups/` con:

- `database.dump`: dump PostgreSQL custom (`pg_dump -Fc`) de toda la base.
- `vault.zip`: vault cifrado + overrides de runtime
  (`.vault_store.json`, `.runtime_config.json`, `.runtime_llm_config.json`).
- `manifest.json`: fecha, commit, tamaños, SHA-256 y conteo de filas de las
  tablas principales.

El zip **sigue conteniendo secretos cifrados**: trátalo como material sensible
y no lo subas a Git (ya está en `.gitignore`).

### 5.2 Crear y verificar un backup

Desde `backend/`:

```powershell
.\.venv\Scripts\python.exe scripts\backup_runtime_data.py --verify
```

En Linux/macOS:

```bash
python scripts/backup_runtime_data.py --verify --keep 14
```

`--verify` restaura el dump en una base temporal
(`trading_db_verify_<timestamp>`) y comprueba las tablas principales; después
la elimina. Nunca toca la base viva.

Opciones útiles:

```text
--output-dir D:/ata-backups   Directorio destino
--keep 14                    Retiene los 14 backups más recientes (0 = todos)
--container ata-postgres     Contenedor PostgreSQL (autodetectado)
--no-container               Fuerza pg_dump/psql locales
```

### 5.3 Programar backups

Windows: el wrapper `scripts/backup-daily.ps1` ejecuta el backup con
verificación, retiene 14 copias y registra todo en `backups/backup-task.log`.
Crea la tarea diaria a las 03:00 (ejecutar como el usuario actual, que es quien
tiene acceso a Docker Desktop):

```powershell
$root = "C:\ruta\ai-trading-assistant"
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
  -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$root\scripts\backup-daily.ps1`""
$trigger = New-ScheduledTaskTrigger -Daily -At 03:00
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
  -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
  -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName "ATA Backup diario" -Action $action `
  -Trigger $trigger -Settings $settings -Principal $principal -Force
```

Verificación:

```powershell
Get-ScheduledTask -TaskName "ATA Backup diario" | Select-Object TaskName, State
Get-ScheduledTaskInfo -TaskName "ATA Backup diario" |
  Select-Object LastRunTime, LastTaskResult, NextRunTime
Get-Content backups\backup-task.log -Tail 20
```

`LastTaskResult = 0` indica éxito. La tarea se ejecuta al volver a estar
disponible si el equipo estaba apagado a las 03:00. Como corre en modo
interactivo, requiere que el usuario haya iniciado sesión para que Docker
Desktop esté accesible.

Linux (cron), diario a las 03:00:

```cron
0 3 * * * cd /opt/ai-trading-assistant/backend && .venv/bin/python scripts/backup_runtime_data.py --verify --keep 14 >> /var/log/ata-backup.log 2>&1
```

Guarda además una copia **off-site** (otro disco o almacenamiento cifrado). Un
backup en el mismo servidor no protege de un fallo de disco.

### 5.4 Restaurar

1. Detén el backend para que no escriba durante la restauración:

   ```powershell
   docker compose -f infra/docker/docker-compose.yml stop backend
   ```

2. Restaura (requiere `--yes`):

   ```powershell
   cd backend
   .\.venv\Scripts\python.exe scripts\restore_runtime_data.py `
     --backup ..\backups\backup-20261026-063607.zip --yes
   ```

3. Arranca el backend y valida:

   ```powershell
   docker compose -f infra/docker/docker-compose.yml start backend
   curl.exe http://127.0.0.1:8000/api/v1/trading/real/status
   ```

El script copia el vault actual a `vault.bak-<timestamp>` antes de sobrescribirlo
y verifica los checksums del manifest antes de tocar nada.

### 5.5 Ensayo de restauración (sin tocar producción)

```powershell
.\.venv\Scripts\python.exe scripts\restore_runtime_data.py `
  --backup ..\backups\backup-....zip --yes --skip-vault `
  --database trading_db_rehearsal --create-database --force
```

Comprueba los conteos y elimina la base de ensayo:

```powershell
docker exec ata-postgres psql -U trading_user -d postgres -c "DROP DATABASE trading_db_rehearsal;"
```

Haz este ensayo al menos una vez al mes y antes de pasar a Mainnet.

---

## 6. OCO nativa de Binance

### 6.1 Activar

Con el backend en `execution_mode=real` y `trading_environment=testnet`:

```powershell
curl.exe -X PUT http://127.0.0.1:8000/api/v1/trading/runtime-config `
  -H "Content-Type: application/json" `
  -d '{"real_trading_oco_enabled": true, "confirm": true}'
```

También puede hacerse desde la UI (panel de Trading Real) o fijando
`REAL_TRADING_OCO_ENABLED=true` en `.env` y reiniciando.

### 6.2 Proteger posiciones ya abiertas

Al arrancar, el backend descubre las OCO existentes y crea una para cada
posición Binance que no tenga. Para forzarlo sin reiniciar:

```powershell
curl.exe -X POST http://127.0.0.1:8000/api/v1/trading/real/oco/ensure
```

Respuesta esperada:

```json
{
  "discovered": 0,
  "created": 1,
  "oco_protected_positions": 1,
  "oco_unprotected_positions": 0,
  "protection_mode": "exchange_oco"
}
```

Es idempotente: las posiciones ya protegidas se omiten y no se duplica ninguna
venta.

### 6.3 Validar tras reiniciar

1. Reinicia el backend.
2. Consulta `/real/status`: `oco_protected_positions` debe coincidir con las
   posiciones abiertas y `oco_unprotected_positions` debe ser `0`.
3. En Binance Testnet, comprueba que existe una lista OCO abierta con las dos
   piernas (TP limit y SL stop-limit) y la cantidad correcta.
4. No abras una entrada nueva hasta confirmarlo.

### 6.4 Fallos y actuación

| Síntoma | Significado | Acción |
|---|---|---|
| `protection_mode=local_guard` | No hay OCO; protege el guardián local | Revisar logs `live_oco_creation_failed`; reintentar `POST /real/oco/ensure` |
| `protection_mode=oco_discovery_failed` | No se pudo confirmar el estado de las OCO | **No vender a mano sin revisar el exchange.** Reiniciar y reintentar; el guardián local bloquea ventas de ese exchange por seguridad |
| `oco_unprotected_positions > 0` | Hay posiciones sin OCO | Ejecutar `POST /real/oco/ensure` y revisar la causa |
| La OCO no se llena en un hueco | El stop-limit quedó lejos del precio | La posición sigue bajo el guardián local; revisar y cerrar manualmente si procede |

Limitaciones: solo Binance spot, una OCO por símbolo, y el stop-limit puede no
ejecutarse en un gap extremo.

---

## 7. Autenticación de la API

Por defecto la API no exige clave y solo escucha en `127.0.0.1`. Para exponerla
más allá de localhost:

1. Genera un token:

   ```powershell
   .\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(48))"
   ```

2. Añade a `backend/.env`:

   ```env
   API_AUTH_TOKEN=<token>
   ```

3. Añade a `frontend/.env` (o `.env.local`) y reconstruye el frontend:

   ```env
   VITE_API_KEY=<el mismo token>
   ```

   ```powershell
   cd frontend
   npm run build
   ```

4. Reinicia el backend.

Comportamiento:

- Toda petición a `/api/*` debe llevar `X-API-Key`.
- `OPTIONS`, `/api/docs`, `/api/redoc`, `/api/openapi.json` y
  `/api/v1/ws/*` quedan exentos (los navegadores no pueden añadir cabeceras al
  handshake WebSocket).
- Una clave incorrecta devuelve `401`.

Rotación: cambia `API_AUTH_TOKEN` y `VITE_API_KEY` a la vez, reconstruye el
frontend y reinicia. El token anterior deja de funcionar de inmediato.

---

## 8. Heartbeat (dead-man's switch)

Un proceso caído no puede enviar su propia alerta. Configura un monitor externo
(healthchecks.io, UptimeRobot push, endpoint propio):

1. Crea un check con periodo de gracia **mayor que 2 × intervalo** (por ejemplo,
   intervalo 60 s y gracia 3 min).
2. Añade a `backend/.env`:

   ```env
   HEARTBEAT_PING_URL=https://hc-ping.com/<uuid>
   HEARTBEAT_INTERVAL_SECONDS=60
   ```

3. Reinicia el backend y confirma en el monitor que llegan pings.
4. Detén el backend unos minutos en una ventana controlada y comprueba que el
   monitor alerta. Vuelve a arrancarlo.

Sin `HEARTBEAT_PING_URL` el servicio queda deshabilitado y se registra
`heartbeat_disabled` en los logs.

---

## 9. Monitorización y alertas

- **Salud:** `GET /health`.
- **Métricas:** `GET /metrics` (Prometheus en `:9091`, Grafana en `:3001`).
- **Estado de trading:** `GET /api/v1/trading/real/status`.
- **Alertas:** `GET /api/v1/alerts/status` y `POST /api/v1/alerts/test`.
- **Logs:** `docker compose -f infra/docker/docker-compose.yml logs -f backend`.

Eventos clave en logs:

- `live_oco_created` / `live_oco_creation_failed` / `live_oco_ensured`
- `live_oco_discovered`
- `live_guard_started` / cierres por SL/TP
- `heartbeat_started` / `heartbeat_ping_failed`
- `reconcile_*` y errores de preflight

Configura Telegram como canal principal y verifica con una alerta de prueba
antes de cada sesión. Una alerta que no llega es peor que no tener alertas
porque crea falsa confianza.

---

## 10. Incidentes

### 10.1 El backend está caído

1. El heartbeat externo (si está configurado) avisa.
2. Las posiciones con OCO siguen protegidas por el exchange.
3. Las posiciones sin OCO quedan **sin protección** hasta que vuelva el proceso.
4. Arranca el backend y revisa `/real/status`; ejecuta `POST /real/oco/ensure`
   si hay posiciones sin OCO.
5. Si no puedes arrancarlo, revisa y cierra las posiciones manualmente en el
   exchange.

### 10.2 Fallo al crear una OCO

- El guardián local sigue protegiendo. No vendas a mano si hay una OCO previa
  posiblemente activa: primero revisa las órdenes abiertas del exchange.
- Reintenta `POST /real/oco/ensure` tras corregir la causa (red, permisos,
  cantidad, SL/TP inválidos).

### 10.3 Descubrimiento de OCO fallido

- El sistema entra en modo fail-safe: no envía ventas locales de ese exchange.
- Revisa manualmente las listas OCO abiertas en Binance.
- Reinicia el backend y confirma que `oco_discovery_failed_exchanges` queda
  vacío antes de operar.

### 10.4 Descuadre de posiciones

1. Detén el motor autónomo (`POST /api/v1/autonomous/stop`).
2. Ejecuta `POST /api/v1/trading/real/reconcile`.
3. Compara con el exchange. Si persiste, no fuerces ventas: investiga la causa
   (orden manual, OCO ejecutada, fill parcial).
4. Restaura un backup solo si el journal está corrupto y has confirmado antes el
   estado real en el exchange.

### 10.5 Exchange o API caídos

- Las entradas nuevas se bloquean por el preflight de conciliación.
- Las salidas se reintentan; no las fuerces a ciegas.
- Si el exchange no responde durante un movimiento fuerte, la OCO sigue en el
  exchange (si estaba colocada). Revisa el estado allí.

### 10.6 Circuit breaker / kill-switch

- El bot deja de abrir posiciones y alerta.
- No lo reinicies por reflejo: revisa la pérdida diaria, el drawdown y las
  posiciones abiertas.
- Reinicia solo tras un análisis manual y documenta la decisión.

### 10.7 Credenciales comprometidas

1. Revoca la API key en el exchange inmediatamente.
2. Crea una nueva **sin permiso de retirada** y con IP allowlist si es posible.
3. Guárdala desde la UI o el endpoint de vault.
4. Rota `API_AUTH_TOKEN` si la exposición incluyó la API del bot.
5. Revisa órdenes y movimientos del exchange.

### 10.8 Pérdida de `API_KEY_ENCRYPTION_KEY`

El vault no se puede descifrar. Recupera la clave del gestor de contraseñas o
vuelve a introducir las API keys. **No** cambies la clave a ciegas: primero
restaura el backup del vault y la clave correspondiente.

---

## 11. Checklist para Mainnet (ejecutada el 2026-10-26 · Mainnet activo)

Mainnet sigue **desactivado**. Esta checklist prepara la decisión, pero no la
toma. El cambio de Testnet a Mainnet se hace **solo desde la interfaz**; no
requiere editar `.env`, reiniciar por un flag nuevo ni modificar código.

### 11.1 Procedimiento solo-frontend

El orden importa:

1. **Guardar claves Mainnet** en Configuración → Cuenta y exchange: elige
   «Mainnet · dinero real» en *Red*, pega las claves y pulsa «Guardar claves».
   Guardar claves no cambia el entorno activo ni las da por verificadas.
2. **Probar la conexión** con Mainnet seleccionado. Es una prueba de lectura:
   ping, saldo y permisos de la clave leídos de Binance. Debe mostrar
   «retiradas no»; si avisa de retiradas, desactívalas en Binance y repite.
3. **Activar el segundo permiso** en Configuración → Límites y protección:
   «Permitir dinero real (Mainnet)». Acepta la confirmación.
4. **Seleccionar Mainnet** en *Red activa*. La interfaz pide confirmación. El
   backend lo rechaza si la clave no está verificada, si permite retiradas o
   si hay posiciones reales abiertas.
5. **Revisar límites** para capital real: máximo por orden, mercados
   permitidos, OCO y permiso del asistente. Pulsa «Guardar límites».
6. **Iniciar la sesión** en el Asistente con *Dónde opera* = «Dinero real».
   La confirmación final debe decir `Binance con DINERO REAL`.

Para volver a Testnet: pausa el bot, cierra o deja constancia de las posiciones,
selecciona Testnet en «Red del exchange» y desmarca el segundo opt-in. No cambies
de entorno con posiciones abiertas.

### 11.2 Condiciones previas

Resultado de la ejecución del 2026-10-26 (cuenta Mainnet vacía, autorizada por
el operador). `[x]` = verificado con evidencia, `[ ]` = pendiente.

- [ ] Testnet ha operado sin incidentes críticos durante un periodo acordado.
      *Decisión del operador: el periodo no está definido.*
- [x] OCO validada en Testnet con apertura, reinicio, ejecución de TP/SL y
      cierre manual. *Validada el 2026-10-23 (ver PROJECT_OVERVIEW §8).*
- [x] Backup + restauración ensayados y verificados al menos una vez.
      *`backup-daily.ps1` restauró en base temporal: orders=16, trades=16,
      decision_logs=53, shadow_scan_runs=168.*
- [x] `API_KEY_ENCRYPTION_KEY` fijada (44 caracteres). *Respaldarla fuera del
      servidor sigue siendo responsabilidad del operador.*
- [x] API solo en loopback: todos los puertos publicados en `127.0.0.1`, por lo
      que `API_AUTH_TOKEN` vacío es aceptable. Las peticiones de escritura desde
      otros orígenes se rechazan (403).
- [x] Heartbeat externo probado: activo cada 60 s y `ping` manual aceptado.
      *La parada controlada no se ensayó para no disparar la alerta externa.*
- [x] Alertas Telegram probadas de extremo a extremo (`delivered=true`).
- [x] Claves Mainnet **sin permiso de retirada** y con IP allowlist,
      confirmado por Binance (`apiRestrictions`): trading sí, retiradas no,
      IP restringida. *El primer intento falló con `-2015` hasta que el
      operador añadió la IP `181.32.182.70` a la clave.*
- [x] Conexión Mainnet verificada en modo lectura: ping ≈285 ms, saldo y
      permisos leídos, sincronización con el exchange correcta.
- [ ] Cuenta Mainnet con saldo spot. *Solo hay 3,05 USDT en Simple Earn
      (`LDUSDT`), no disponibles para spot; Binance exige ~5 USDT por orden.
      El bot no puede ejecutar nada hasta que se depositen fondos en spot.*
- [x] `REAL_TRADING_MAX_NOTIONAL` (50 USDT) y allowlist (BTC, ETH, BNB, SOL,
      XRP) revisados. Ajustar antes de depositar capital.
- [x] Kill-switch probado: sesión simulada iniciada y detenida con
      `emergency-kill`, 0 posiciones abiertas antes y después.
- [x] Cambio a Mainnet con confirmación y 0 posiciones abiertas, por los
      mismos endpoints que usa la interfaz. Antes de verificar la clave, la
      salvaguarda lo rechazó (403) sin cambiar nada, como debe.
- [x] Sesión real en Mainnet iniciada con ejecución automática desactivada:
      escaneó 5 mercados con datos reales, 0 órdenes, detenida sin incidencias.
      Guardián activo y descubrimiento OCO correcto.
- [ ] Revisión fiscal/legal y tolerancia a pérdida definidas por el operador.

**Fallos encontrados y corregidos durante la ejecución**

- *Mainnet no podía firmar ninguna petición*: el reloj del contenedor iba
  ~1,9 s adelantado y CCXT, en Binance Mainnet, llama a un endpoint firmado
  dentro de `load_markets()` antes de sincronizar la hora. El adaptador ahora
  sincroniza el reloj antes de cargar mercados. Testnet no lo mostraba porque
  allí ese endpoint no se usa.
- *Permisos de la clave nunca verificados*: guardar una clave marcaba
  «verificada», «puede operar» y «sin retiradas» sin preguntar a Binance.
  «Probar conexión» lee ahora `GET /sapi/v1/account/apiRestrictions` y
  registra trading, retiradas e IP. Activar Mainnet exige esa verificación y
  rechaza claves con retiradas.

- *Saldo de Simple Earn sin valorar*: `LDUSDT`/`LDBTC` aparecían «sin precio»
  y el patrimonio salía en 0. Ahora se valoran por su activo base.

**Estado tras la ejecución:** Mainnet activo, permiso de dinero real activo,
asistente detenido, 0 posiciones, máximo 50 USDT por orden. Antes de depositar
capital en spot: decide el periodo de prueba, la tolerancia a pérdida y la
revisión fiscal, y ajusta el máximo por orden. Para volver a Testnet:
Configuración → Límites y protección → *Red activa* = Testnet.

El bot nunca cambia a Mainnet por sí solo y no se debe asumir rentabilidad.

---

## 12. Verificación técnica vigente

- **406 tests** aprobados, cobertura **80,23 %**.
- Ruff (formato y lint) y MyPy sin errores.
- `npm run build` correcto.
- Backup real creado y verificado restaurando en base temporal:
  `orders=15`, `positions=1`, `trades=15`, `portfolio_snapshots=17`,
  `decision_logs=50`, `shadow_scan_runs=150`.
- Restauración de ensayo en `trading_db_rehearsal` verificada y base eliminada.

Validación en vivo (2026-10-26): con una posición BTC/USDT abierta se activó
`real_trading_oco_enabled=true`; `POST /real/oco/ensure` creó la OCO
(`created=1`, `protection_mode=exchange_oco`, `oco_unprotected_positions=0`), un
segundo intento devolvió `created=0` (idempotente) y, tras reiniciar el backend,
los logs mostraron `live_oco_discovered count=1` y
`live_oco_ensured_at_startup count=0`. La OCO 246386 en Binance Testnet tiene
las piernas `STOP_LOSS_LIMIT` (stop 79.247,22 / límite 79.167,97) y
`LIMIT_MAKER` (84.099,09), cantidad 0,00061.

---

## 13. Limitaciones conocidas

- La OCO solo cubre Binance spot y una por símbolo; el stop-limit puede no
  llenarse en un gap.
- El guardián local solo protege con el backend encendido.
- El journaling es best-effort; el exchange manda.
- Una sola instancia del backend; no hay alta disponibilidad.
- Los proveedores LLM gratuitos tienen cuotas y cooldowns; sin proveedor, el
  comité usa análisis determinista.
- Sin garantía de rentabilidad. Este sistema ejecuta reglas y controles de
  riesgo; no predice el mercado.
