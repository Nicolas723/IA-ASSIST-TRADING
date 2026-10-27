#!/usr/bin/env bash
# Prepare a fresh Ubuntu 22.04/24.04 VPS for the trading assistant.
# Idempotent: safe to run again. Run as root:  sudo bash scripts/vps-setup.sh
#
# Installs Docker, opens only SSH/HTTP/HTTPS, keeps the clock synced (Binance
# rejects signed requests from a drifting clock), adds swap on small servers,
# enables automatic security updates and schedules the verified daily backup.
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ $EUID -ne 0 ]]; then
  echo "Run as root: sudo bash scripts/vps-setup.sh" >&2
  exit 1
fi

echo "==> System packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y ca-certificates curl git ufw chrony python3 unattended-upgrades

echo "==> Docker"
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
fi
systemctl enable --now docker

echo "==> Clock sync (chrony)"
systemctl enable --now chrony
timedatectl set-ntp true || true

echo "==> Firewall: only SSH, HTTP and HTTPS"
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 443/udp
ufw --force enable

echo "==> Swap (only if RAM < 3 GB and no swap yet)"
mem_kb=$(awk '/MemTotal/ {print $2}' /proc/meminfo)
if [[ $mem_kb -lt 3000000 ]] && ! swapon --show | grep -q .; then
  fallocate -l 2G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

echo "==> Automatic security updates"
dpkg-reconfigure -f noninteractive unattended-upgrades

echo "==> Daily verified backup at 03:00 (server time), 14 copies kept"
mkdir -p "$APP_DIR/backups"
cron_line="0 3 * * * cd $APP_DIR && python3 backend/scripts/backup_runtime_data.py --container ata-postgres --verify --keep 14 >> backups/backup-task.log 2>&1"
( crontab -l 2>/dev/null | grep -v 'backup_runtime_data.py' ; echo "$cron_line" ) | crontab -

echo
echo "Done. Public IP of this server (add it to your Binance API key allowlist):"
curl -fsS https://api.ipify.org || true
echo
