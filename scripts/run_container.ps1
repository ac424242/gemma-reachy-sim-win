# Launch the lerobot-gpu container with GPU + X11 forwarding to VcXsrv.
#
# Prerequisites on Windows:
#   1. Install VcXsrv, run XLaunch, and CHECK "Disable access control".
#   2. Docker Desktop running with the WSL2/NVIDIA GPU integration enabled.
#
# This script sets DISPLAY for X11 forwarding to VcXsrv and bind-mounts the repo
# at /workspace inside the container so the python_control code is editable from
# Windows.
#
# DISPLAY uses host.docker.internal:0.0 - this was validated end-to-end against
# VcXsrv (xdpyinfo + xeyes connected successfully) and is more reliable than a
# hand-detected IPv4. Override with: $env:DISPLAY_TARGET = "<ip>:0.0"
#
# Usage (from the repo root):
#   ./scripts/run_container.ps1

$ErrorActionPreference = "Stop"

# Confirmed-working display target; allow override via env var.
$display = if ($env:DISPLAY_TARGET) { $env:DISPLAY_TARGET } else { "host.docker.internal:0.0" }

# Informational only: the host adapter VcXsrv is reachable on.
$ip = (Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" } |
    Select-Object -First 1).IPAddress

$repo = (Resolve-Path "$PSScriptRoot/..").Path

Write-Host "Windows IPv4 : $ip (informational)"
Write-Host "DISPLAY      : $display"
Write-Host "Repo mount   : $repo -> /workspace"
Write-Host ""
Write-Host "Inside the container (first time only - installs the full Reachy stack):"
Write-Host "  bash /workspace/scripts/container_setup.sh"
Write-Host ""
Write-Host "Then, in two shells (use 'docker exec -u root reachy-sim bash' for a 2nd shell):"
Write-Host "  DISPLAY=$display reachy-mini-daemon --sim                 # terminal 1: MuJoCo window"
Write-Host "  cd /workspace/python_control && python control_script.py  # terminal 2: Gemma loop"
Write-Host ""
Write-Host "Runs as -u root so apt-get works (the image's default user cannot install packages)."
Write-Host ""

# Named + persistent so a second 'docker exec' shell can attach for the control loop.
docker run --gpus all --name reachy-sim `
    -u root `
    -e DISPLAY=$display `
    -e OLLAMA_HOST=http://host.docker.internal:11434 `
    -v "$($repo):/workspace" `
    -it huggingface/lerobot-gpu /bin/bash
