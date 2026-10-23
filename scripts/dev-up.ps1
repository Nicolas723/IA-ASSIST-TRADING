#!/usr/bin/env pwsh
# Levanta el stack de Docker (Postgres, Redis, Backend, Prometheus, Grafana).
# Resuelve las rutas a partir de la ubicación de ESTE script, así que funciona
# sin importar dónde esté el proyecto o desde qué carpeta se ejecute.
$ErrorActionPreference = "Stop"

$root = Resolve-Path (Join-Path $PSScriptRoot "..")
$composeFile = Join-Path $root "infra/docker/docker-compose.yml"

Write-Host "Raíz del proyecto: $root"
# No se usa --project-directory: las rutas relativas del compose se resuelven
# respecto a la carpeta del propio archivo compose (infra/docker).
docker compose -f $composeFile up -d
