# Despliegue en un VPS (funcionamiento 24/7)

Guía para mover el asistente de tu PC a un servidor pequeño en la nube, de modo
que funcione aunque apagues el ordenador. Al terminar tendrás la interfaz en
`https://<tu-dominio>` protegida con usuario y contraseña.

> **Antes de empezar:** el VPS tendrá otra IP pública. Si tu clave de Binance
> está restringida por IP (debe estarlo), **añade la IP del VPS** en Binance →
> Gestión de API, o Mainnet devolverá `-2015` como ocurrió el 2026-10-26.

## 1. Qué servidor contratar

Basta con **2 vCPU y 2–4 GB de RAM**, Ubuntu 24.04. Referencias orientativas
(revisa precios actuales):

| Proveedor | Plan | Notas |
|---|---|---|
| Hetzner Cloud | CX22 (2 vCPU, 4 GB) | Muy buena relación precio/recursos; centros de datos en EU y EE. UU. |
| DigitalOcean | Basic 2 GB | Panel sencillo |
| Vultr / Linode | 2 GB | Alternativas equivalentes |

La región no es crítica: unas decenas de milisegundos de latencia hasta
Binance no afectan a esta estrategia, que decide cada varios minutos.

## 2. Nombre del servidor (dominio)

Caddy necesita un nombre para emitir el certificado HTTPS gratis:

- **Con dominio propio:** crea un registro `A` (p. ej. `bot.tudominio.com`)
  apuntando a la IP del VPS.
- **Sin dominio:** usa `sslip.io`, que resuelve cualquier IP gratis. Si la IP
  es `203.0.113.10`, el nombre es `203-0-113-10.sslip.io`.

## 3. Preparar el servidor

```bash
ssh root@<IP_DEL_VPS>
git clone <URL_DE_TU_REPO> /opt/ai-trading-assistant
cd /opt/ai-trading-assistant
sudo bash scripts/vps-setup.sh
```

El script instala Docker, deja abiertos solo SSH/80/443, sincroniza el reloj
(Binance rechaza peticiones con el reloj desfasado), añade swap si hay poca
RAM, activa actualizaciones de seguridad y programa el backup diario
verificado. Al final imprime la IP pública del servidor.

## 4. Copiar secretos y datos desde tu PC

Haz un backup verificado en tu PC y súbelo junto con el `.env`:

```powershell
# En tu PC (PowerShell, raíz del repositorio)
powershell -File scripts\backup-daily.ps1
scp backend\.env root@<IP_DEL_VPS>:/opt/ai-trading-assistant/backend/.env
scp backups\<ultimo-backup>.zip root@<IP_DEL_VPS>:/opt/ai-trading-assistant/backups/
```

El `.env` contiene `API_KEY_ENCRYPTION_KEY`: es la clave que descifra el vault.
Sin ella las claves guardadas no sirven. Guarda también una copia fuera de
ambos equipos.

## 5. Configurar el despliegue

```bash
cd /opt/ai-trading-assistant/infra/docker
cp .env.prod.example .env.prod
docker run --rm caddy:2-alpine caddy hash-password --plaintext 'TU-CONTRASEÑA'
openssl rand -base64 32        # contraseña de la base de datos
nano .env.prod
```

Rellena `DOMAIN`, `ACME_EMAIL`, `BASIC_AUTH_HASH` (escribe cada `$` como `$$`)
y `POSTGRES_PASSWORD`.

## 6. Arrancar

```bash
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build
docker compose -f docker-compose.prod.yml --env-file .env.prod ps
```

La primera construcción tarda unos minutos. La app aplica las migraciones al
arrancar. Caddy obtiene el certificado en el primer acceso.

## 7. Restaurar tus datos

Con el stack arriba, restaura el backup (historial, decisiones y vault):

```bash
cd /opt/ai-trading-assistant
python3 backend/scripts/restore_runtime_data.py \
  --backup backups/<ultimo-backup>.zip --container ata-postgres --yes
docker compose -f infra/docker/docker-compose.prod.yml --env-file infra/docker/.env.prod restart app
```

## 8. Comprobar

1. Abre `https://<DOMAIN>`: el navegador pide usuario y contraseña.
2. **Configuración → Cuenta y exchange → Probar conexión** con Mainnet: debe
   mostrar «retiradas no» e «IP restringida». Si da `-2015`, falta la IP del
   VPS en la clave de Binance.
3. **Configuración → Alertas → Enviar prueba** (Telegram) y **Probar ping**
   (heartbeat).
4. Deja el asistente en simulado unas horas y revisa **Operaciones**.

## 9. Apagar el bot de tu PC

Cuando el VPS funcione, **detén el stack local** para no tener dos guardianes
vigilando las mismas claves:

```powershell
docker compose -f infra/docker/docker-compose.yml down
```

(Sin `-v`: así conservas los datos locales.)

## Operación diaria

| Tarea | Comando (en `/opt/ai-trading-assistant/infra/docker`) |
|---|---|
| Ver estado | `docker compose -f docker-compose.prod.yml --env-file .env.prod ps` |
| Ver logs | `docker compose -f docker-compose.prod.yml --env-file .env.prod logs -f app` |
| Actualizar | `git pull && docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build` |
| Reiniciar | `docker compose -f docker-compose.prod.yml --env-file .env.prod restart app` |
| Backup manual | `cd ../.. && python3 backend/scripts/backup_runtime_data.py --container ata-postgres --verify` |

Los backups quedan en `/opt/ai-trading-assistant/backups/`. Descárgalos de vez
en cuando a otro lugar: si el VPS desaparece, se van con él.

## Seguridad

- Solo Caddy está expuesto (80/443). PostgreSQL, Redis y la app no son
  accesibles desde internet.
- Todo, incluida la API y el WebSocket, exige el usuario y la contraseña.
- La protección anti-CSRF del backend sigue activa detrás del proxy.
- Usa claves SSH para entrar al VPS y desactiva el acceso por contraseña.
