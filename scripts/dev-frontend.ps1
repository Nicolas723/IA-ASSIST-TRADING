#!/usr/bin/env pwsh
# Levanta el frontend (Vite) desde cualquier ubicación.
$ErrorActionPreference = "Stop"

$root = Resolve-Path (Join-Path $PSScriptRoot "..")
$frontend = Join-Path $root "frontend"

Write-Host "Frontend: $frontend"
Push-Location $frontend
try {
    npm run dev
} finally {
    Pop-Location
}
