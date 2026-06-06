<#
.SYNOPSIS
  One-click startup: Docker, VcXsrv, Ollama, sim, voice UI, and chat loop.

.DESCRIPTION
  Attempts to bring up everything needed to talk to the robot in the browser:
    - Docker Desktop (starts it if stopped)
    - VcXsrv X server (MuJoCo window on Windows)
    - Ollama + gemma3:4b (docker compose)
    - voice container (http://localhost:7860)
    - reachy-sim container + one-time Reachy stack setup
    - reachy-mini-daemon --sim (MuJoCo visualization)
    - chat control loop (STT=1, browser Piper TTS via replies/)

  Idempotent: safe to re-run; reuses running services where possible.

.PARAMETER CameraSource
  Optional live frame each chat turn, e.g. http://192.168.4.159:8080/shot.jpg
  (implies camera). Use http:// not https:// for IP Webcam apps.

.PARAMETER NoBrowser
  Do not auto-open http://localhost:7860 when ready.

.PARAMETER NoVoice
  Skip the voice container (text-only chat in the container log).

.PARAMETER DisplayTarget
  X11 display for MuJoCo forwarding. Default: host.docker.internal:0.0

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\start_robot.ps1

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\start_robot.ps1 `
    -CameraSource http://192.168.4.159:8080/shot.jpg
#>
param(
    [string]$CameraSource = "",
    [switch]$NoBrowser,
    [switch]$NoVoice,
    [string]$DisplayTarget = "host.docker.internal:0.0"
)

# Docker writes progress to stderr; Continue avoids false stops (we check $LASTEXITCODE).
$ErrorActionPreference = "Continue"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$vcxPath = "C:\Program Files\VcXsrv\vcxsrv.exe"
$dockerDesktop = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
$nortonCert = Join-Path $repo "norton_root.crt"
$voiceUrl = "http://localhost:7860"
$dockerFmt = '{{.Server.Version}}'

function Write-Step([string]$msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Write-Ok([string]$msg)   { Write-Host "    OK: $msg" -ForegroundColor Green }
function Write-Warn([string]$msg) { Write-Host "    WARN: $msg" -ForegroundColor Yellow }

function Wait-DockerDaemon {
    param([int]$TimeoutSec = 180)
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        docker info --format "{{.ServerVersion}}" 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { return }
        Start-Sleep -Seconds 4
    }
    throw "Docker daemon not ready after ${TimeoutSec}s. Is Docker Desktop running?"
}

function Ensure-DockerDesktop {
    Write-Step "Docker Desktop"
    docker info --format "{{.ServerVersion}}" 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
        $ver = docker version --format $dockerFmt 2>$null
        Write-Ok "daemon already up ($ver)"
        return
    }
    if (-not (Test-Path $dockerDesktop)) {
        throw "Docker Desktop not found at $dockerDesktop"
    }
    Start-Process $dockerDesktop | Out-Null
    Write-Warn "launching Docker Desktop - waiting for daemon..."
    Wait-DockerDaemon
    Write-Ok "daemon ready"
}

function Ensure-VcXsrv {
    Write-Step "VcXsrv (X server for MuJoCo)"
    if (-not (Test-Path $vcxPath)) {
        throw "VcXsrv not found. Install from https://sourceforge.net/projects/vcxsrv/"
    }
    if ((Get-Process vcxsrv -ErrorAction SilentlyContinue | Measure-Object).Count -gt 0) {
        Write-Ok "already running"
        return
    }
    Start-Process $vcxPath -ArgumentList ":0", "-multiwindow", "-clipboard", "-wgl", "-ac" | Out-Null
    Start-Sleep -Seconds 2
    Write-Ok "started (Disable access control is enabled via -ac)"
}

function Ensure-NortonCertOnHost {
    if (Test-Path $nortonCert) { return }
    Write-Warn "Exporting Norton TLS root CA (needed when antivirus MITM breaks container HTTPS)..."
    $certs = Get-ChildItem Cert:\LocalMachine\Root, Cert:\CurrentUser\Root -ErrorAction SilentlyContinue |
        Where-Object { $_.Subject -like "*Norton*" }
    if (-not $certs) {
        Write-Warn "No Norton cert in Windows store - skipping export (fine if HTTPS works)"
        return
    }
    $pem = ""
    foreach ($c in ($certs | Select-Object -Unique)) {
        $pem += "-----BEGIN CERTIFICATE-----`n"
        $pem += [System.Convert]::ToBase64String($c.RawData, [System.Base64FormattingOptions]::InsertLineBreaks)
        $pem += "`n-----END CERTIFICATE-----`n"
    }
    Set-Content -Path $nortonCert -Value $pem -Encoding ascii
    Write-Ok "wrote $nortonCert"
}

function Install-NortonCaInContainer {
    if (-not (Test-Path $nortonCert)) { return }
    $cmd = 'cp /workspace/norton_root.crt /usr/local/share/ca-certificates/norton_root.crt && update-ca-certificates'
    docker exec -u root reachy-sim bash -lc $cmd 2>$null | Out-Null
}

function Test-ReachyStackInstalled {
    docker exec reachy-sim /lerobot/.venv/bin/python -c "import reachy_mini, ollama, cv2" 2>$null | Out-Null
    return ($LASTEXITCODE -eq 0)
}

function Install-ReachyStack {
    Write-Step "Reachy stack setup (first run only)"
    Install-NortonCaInContainer

    # Strip Windows CRLF before bash runs (bind-mounted scripts are edited on Windows).
    $setupCmd = "sed 's/\r$//' /workspace/scripts/container_setup.sh > /tmp/setup.sh && bash /tmp/setup.sh"
    docker exec -u root reachy-sim bash -lc $setupCmd 2>&1 | Out-Host
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "container_setup.sh failed - retrying pip deps with --system-certs..."
        docker exec -u root -e SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt reachy-sim `
            uv pip install --system-certs --python /lerobot/.venv/bin/python `
            'reachy-mini[mujoco]' ollama opencv-python-headless numpy 2>&1 | Out-Host
        if ($LASTEXITCODE -ne 0) { throw "Reachy stack install failed" }
    }
    if (-not (Test-ReachyStackInstalled)) {
        throw "Reachy stack install finished but imports still fail"
    }
    Write-Ok "reachy-mini + loop deps installed"
}

function Ensure-ReachySimContainer {
    Write-Step "reachy-sim container"
    $exists = docker ps -a --filter "name=^reachy-sim$" --format "{{.Names}}" 2>$null
    if ($exists -eq "reachy-sim") {
        $running = docker ps --filter "name=^reachy-sim$" --format "{{.Names}}" 2>$null
        if ($running -ne "reachy-sim") {
            docker start reachy-sim | Out-Null
            Start-Sleep -Seconds 2
        }
        Write-Ok "using existing reachy-sim"
        return
    }
    docker run -d --gpus all --name reachy-sim -u root `
        -e DISPLAY=$DisplayTarget `
        -e OLLAMA_HOST=http://host.docker.internal:11434 `
        -v "${repo}:/workspace" `
        huggingface/lerobot-gpu tail -f /dev/null | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Failed to create reachy-sim container" }
    Start-Sleep -Seconds 2
    Write-Ok "created reachy-sim"
}

function Ensure-Ollama {
    Write-Step "Ollama (Gemma 3)"
    Push-Location $repo
    try {
        docker compose up -d ollama 2>&1 | Out-Host
        if ($LASTEXITCODE -ne 0) { throw "docker compose up ollama failed" }
    } finally {
        Pop-Location
    }

    $deadline = (Get-Date).AddSeconds(60)
    do {
        Start-Sleep -Seconds 2
        try {
            $r = Invoke-WebRequest -Uri "http://localhost:11434/api/tags" -UseBasicParsing -TimeoutSec 5
        } catch { $r = $null }
    } while ((-not $r) -and (Get-Date) -lt $deadline)
    if (-not $r) { throw "Ollama API not reachable on :11434" }
    Write-Ok "API up"

    $models = docker exec ollama ollama list 2>&1 | Out-String
    if ($models -notmatch "gemma3:4b") {
        Write-Warn "pulling gemma3:4b (one-time, ~3 GB)..."
        docker exec ollama ollama pull gemma3:4b 2>&1 | Out-Host
        if ($LASTEXITCODE -ne 0) { throw "ollama pull gemma3:4b failed" }
    }
    Write-Ok "gemma3:4b available"
}

function Ensure-VoiceContainer {
    if ($NoVoice) {
        Write-Step "Voice container (skipped -NoVoice)"
        return
    }
    Write-Step "Voice container (browser listen/talk)"
    New-Item -ItemType Directory -Force -Path (Join-Path $repo "stt_in") | Out-Null
    New-Item -ItemType Directory -Force -Path (Join-Path $repo "replies") | Out-Null
    Push-Location $repo
    try {
        docker compose up -d --build voice 2>&1 | Out-Host
        if ($LASTEXITCODE -ne 0) { throw "docker compose up voice failed" }
    } finally {
        Pop-Location
    }
    $deadline = (Get-Date).AddSeconds(90)
    do {
        Start-Sleep -Seconds 2
        try {
            $r = Invoke-WebRequest -Uri "$voiceUrl/health" -UseBasicParsing -TimeoutSec 5
        } catch { $r = $null }
    } while ((-not $r) -and (Get-Date) -lt $deadline)
    if (-not $r) { throw "Voice service not reachable at $voiceUrl" }
    Write-Ok "listening at $voiceUrl"
}

function Initialize-WindowFocus {
    $winFocusType = [System.Management.Automation.PSTypeName]'WinFocus'
    if ($winFocusType.Type) { return }
    Add-Type @"
using System;
using System.Runtime.InteropServices;
using System.Text;
public class WinFocus {
    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc lpEnumFunc, IntPtr lParam);
    [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr hWnd, StringBuilder sb, int count);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
    public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
    public static string MatchedTitle = "";
    public static bool Callback(IntPtr hWnd, IntPtr lParam) {
        if (!IsWindowVisible(hWnd)) return true;
        var sb = new StringBuilder(512);
        GetWindowText(hWnd, sb, 512);
        string t = sb.ToString();
        if (t.Length == 0) return true;
        string low = t.ToLowerInvariant();
        if (low.Contains("mujoco") || (low.Contains("reachy") && !low.Contains("chrome"))) {
            ShowWindow(hWnd, 9);
            SetForegroundWindow(hWnd);
            MatchedTitle = t;
            return false;
        }
        return true;
    }
}
"@
}

function Present-VisualizationWindow {
    Initialize-WindowFocus
    Write-Step "MuJoCo visualization window"
    $deadline = (Get-Date).AddSeconds(10)
    while ((Get-Date) -lt $deadline) {
        [WinFocus]::MatchedTitle = ""
        [WinFocus]::EnumWindows([WinFocus+EnumWindowsProc]{ param($h, $l) [WinFocus]::Callback($h, $l) }, [IntPtr]::Zero) | Out-Null
        if ([WinFocus]::MatchedTitle) {
            Write-Ok "brought to front: $([WinFocus]::MatchedTitle)"
            return $true
        }
        Start-Sleep -Milliseconds 600
    }
    # Fallback: partial-match activate via shell (VcXsrv-hosted windows)
    $shell = New-Object -ComObject WScript.Shell
    foreach ($hint in @("MuJoCo", "Reachy", "reachy", "simulation")) {
        if ($shell.AppActivate($hint)) {
            Write-Ok "activated window matching: $hint"
            return $true
        }
    }
    Write-Warn "sim window not found yet - check the taskbar for a MuJoCo / VcXsrv window"
    return $false
}

function Test-SimDaemonHealthy {
    docker exec -u root reachy-sim bash -lc "curl -sf -m 3 http://127.0.0.1:8000/ >/dev/null" 2>$null | Out-Null
    return ($LASTEXITCODE -eq 0)
}

function Start-SimDaemon {
    Write-Step "MuJoCo sim daemon"
    if (Test-SimDaemonHealthy) {
        Write-Ok "daemon already running"
        Present-VisualizationWindow | Out-Null
        return
    }
    docker exec -u root reachy-sim bash -lc "pkill -f 'reachy-mini-daemon.*--sim'" 2>$null | Out-Null
    $daemonCmd = "DISPLAY=$DisplayTarget LIBGL_ALWAYS_SOFTWARE=1 reachy-mini-daemon --sim > /tmp/daemon.log 2>&1"
    docker exec -d -u root reachy-sim bash -lc $daemonCmd
    $deadline = (Get-Date).AddSeconds(30)
    $ready = $false
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 2
        if (Test-SimDaemonHealthy) { $ready = $true; break }
    }
    $log = docker exec reachy-sim bash -lc "tail -n 5 /tmp/daemon.log 2>/dev/null" 2>&1 | Out-String
    if ($ready -or ($log -match "Daemon started successfully")) {
        Write-Ok "daemon started"
    } else {
        Write-Warn "daemon may not be ready - check /tmp/daemon.log in reachy-sim"
    }
    Present-VisualizationWindow | Out-Null
}

function Start-ChatLoop {
    Write-Step "Chat control loop (robot brain + actuation)"
    if (-not (Test-SimDaemonHealthy)) {
        Write-Warn "sim daemon not reachable on :8000 - chat will run in dry-run mode"
    }
    docker exec -u root reachy-sim bash -lc "pkill -f control_script.py" 2>$null | Out-Null
    Start-Sleep -Seconds 1

    $envs = @(
        "INPUT_MODE=chat",
        "STT=1",
        "TTS=0",
        "STT_DIR=/workspace/stt_in",
        "REPLY_DIR=/workspace/replies",
        "OLLAMA_HOST=http://host.docker.internal:11434"
    )
    if ($CameraSource) {
        $envs += "CHAT_USE_CAMERA=1"
        $envs += "CAMERA_SOURCE=$CameraSource"
    }
    $envLine = ($envs -join " ")
    $chatCmd = 'cd /workspace/python_control && ' + $envLine + ' /lerobot/.venv/bin/python control_script.py > /tmp/chat.log 2>&1'
    docker exec -d -u root reachy-sim bash -lc $chatCmd
    Start-Sleep -Seconds 3
    $log = docker exec reachy-sim bash -lc "tail -n 8 /tmp/chat.log 2>/dev/null" 2>&1 | Out-String
    if ($log -match "Connected to Reachy Mini|Voice chat|Chat with the robot") {
        Write-Ok "chat loop running (log: /tmp/chat.log in reachy-sim)"
    } else {
        Write-Warn "chat loop started; tail /tmp/chat.log if voice page times out"
    }
}

function Show-Summary {
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Green
    Write-Host " Reachy is up" -ForegroundColor Green
    Write-Host "========================================" -ForegroundColor Green
    Write-Host ""
    Write-Host "  MuJoCo sim  : VcXsrv window (head + antennas)"
    if (-not $NoVoice) {
        Write-Host "  Voice UI    : $voiceUrl"
    }
    Write-Host "  Ollama      : http://localhost:11434"
    if ($CameraSource) {
        Write-Host "  Camera      : $CameraSource"
    }
    Write-Host ""
    Write-Host "  Logs (inside reachy-sim):"
    Write-Host "    docker exec reachy-sim tail -f /tmp/chat.log"
    Write-Host "    docker exec reachy-sim tail -f /tmp/daemon.log"
    Write-Host ""
    Write-Host "  Stop everything: scripts\stop_robot.ps1  (or StopReachy.bat)"
    Write-Host ""
}

function Prompt-UserInterface {
    Write-Host ""
    Write-Host "----------------------------------------" -ForegroundColor Yellow
    Write-Host " Ready - use these two interfaces" -ForegroundColor Yellow
    Write-Host "----------------------------------------" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  1. SIM  - MuJoCo window (Reachy head + antennas)"
    Present-VisualizationWindow | Out-Null
    Write-Host "     If you do not see it, check the taskbar for MuJoCo or VcXsrv."
    Write-Host ""

    if ($NoVoice) { return }

    Write-Host "  2. VOICE  - open in your browser:"
    Write-Host "       $voiceUrl" -ForegroundColor Cyan
    Write-Host "     Hold the button on that page, speak, release."
    Write-Host ""

    if ($NoBrowser) {
        Write-Host "  Open that URL manually when you are ready."
        return
    }

    $answer = Read-Host "Open $voiceUrl in your browser now? [Y/n]"
    if ($answer -eq "" -or $answer -match "^[yY]") {
        Start-Process $voiceUrl | Out-Null
        Write-Ok "browser opened"
    } else {
        Write-Host "  OK - open $voiceUrl yourself when ready."
    }
}

# --- main ---
try {
    Write-Host "Reachy one-click startup" -ForegroundColor White
    Write-Host "Repo: $repo"

    Ensure-DockerDesktop
    Ensure-VcXsrv
    Ensure-NortonCertOnHost
    Ensure-Ollama
    Ensure-ReachySimContainer
    Install-NortonCaInContainer
    if (-not (Test-ReachyStackInstalled)) {
        Install-ReachyStack
    } else {
        Write-Step "Reachy stack"
        Write-Ok "already installed"
    }
    Start-SimDaemon
    Ensure-VoiceContainer
    Start-ChatLoop

    Show-Summary
    Prompt-UserInterface
    exit 0
} catch {
    Write-Host ""
    Write-Host "STARTUP FAILED: $_" -ForegroundColor Red
    Write-Host ""
    exit 1
}
