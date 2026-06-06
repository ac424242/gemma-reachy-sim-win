<#
.SYNOPSIS
  Stop the Reachy stack started by start_robot.ps1.

.DESCRIPTION
  Stops in-container processes, removes reachy-sim, and runs docker compose down.
  VcXsrv is left running (you may close it manually).
#>
$ErrorActionPreference = "SilentlyContinue"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

Write-Host "Stopping Reachy..." -ForegroundColor Cyan

docker exec -u root reachy-sim bash -lc "pkill -f control_script.py; pkill -f reachy-mini-daemon" 2>$null
docker rm -f reachy-sim 2>$null

Push-Location $repo
docker compose down 2>&1 | Out-Host
Pop-Location

Write-Host "Done. VcXsrv was left running - close it from the taskbar if you want." -ForegroundColor Green
