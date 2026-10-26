#!/usr/bin/env pwsh
# Ejecuta el backup diario del runtime y lo registra en backups/backup-task.log.
# Pensado para el Programador de tareas de Windows, pero se puede ejecutar a mano.
#
# Requisitos:
#   - Docker Desktop iniciado (o el stack ya levantado).
#   - El contenedor ata-postgres disponible.
#
# Uso:
#   .\scripts\backup-daily.ps1

$ErrorActionPreference = "Stop"

$root = Resolve-Path (Join-Path $PSScriptRoot "..")
$backend = Join-Path $root "backend"
$python = Join-Path $backend ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    $python = "python"
}
$backupScript = Join-Path $backend "scripts\backup_runtime_data.py"
$backups = Join-Path $root "backups"
$log = Join-Path $backups "backup-task.log"

New-Item -ItemType Directory -Force -Path $backups | Out-Null

# Rota el log si supera 5 MB.
if ((Test-Path $log) -and ((Get-Item $log).Length -gt 5MB)) {
    Move-Item -Force $log "$log.1"
}

function Write-Log {
    param([string]$Message)
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Add-Content -Path $log -Value $line -Encoding utf8
    Write-Host $line
}

Write-Log "Inicio del backup"

# Espera a que Docker responda (hasta 5 minutos).
$dockerReady = $false
for ($i = 0; $i -lt 30; $i++) {
    try {
        & docker info 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) {
            $dockerReady = $true
            break
        }
    } catch {
        # Docker todavía no está disponible.
    }
    Start-Sleep -Seconds 10
}
if (-not $dockerReady) {
    Write-Log "ERROR: Docker no responde; no se puede hacer el backup."
    exit 1
}

# Espera a PostgreSQL; si el contenedor existe pero está parado, lo arranca.
$pgReady = $false
for ($i = 0; $i -lt 12; $i++) {
    try {
        $state = (& docker inspect -f "{{.State.Running}}" ata-postgres 2>$null).Trim()
        if ($state -eq "true") {
            $pgReady = $true
            break
        }
    } catch {
        # Contenedor inexistente o Docker aún arrancando.
    }
    Start-Sleep -Seconds 5
}

if (-not $pgReady) {
    Write-Log "PostgreSQL no está corriendo; intentando iniciar ata-postgres."
    try {
        & docker start ata-postgres 2>$null | Out-Null
    } catch {
        Write-Log "ERROR: no se pudo iniciar ata-postgres."
        exit 1
    }
    for ($i = 0; $i -lt 12; $i++) {
        try {
            $state = (& docker inspect -f "{{.State.Running}}" ata-postgres 2>$null).Trim()
            if ($state -eq "true") {
                $pgReady = $true
                break
            }
        } catch {
            # Sigue esperando.
        }
        Start-Sleep -Seconds 5
    }
}

if (-not $pgReady) {
    Write-Log "ERROR: PostgreSQL no está disponible; no se puede hacer el backup."
    exit 1
}

Push-Location $backend
try {
    $output = & $python $backupScript --verify --keep 14 --output-dir $backups 2>&1
    $code = $LASTEXITCODE
    $output | ForEach-Object { Write-Log $_ }
} finally {
    Pop-Location
}

if ($code -eq 0) {
    Write-Log "Backup completado correctamente."
} else {
    Write-Log "ERROR: el backup terminó con código $code."
}
exit $code
