<#
.SYNOPSIS
  One command to talk to the robot AND hear it.

.DESCRIPTION
  Starts the speaker player (scripts\play_tts.ps1) in a minimized background
  window, then opens the interactive chat inside the reachy-sim container. When
  you quit the chat, the player is stopped automatically. No second window to
  manage.

  Requires the container (reachy-sim) to be running. The sim is optional - if
  reachy-mini-daemon --sim is up you'll also see the head/antennas react.

.PARAMETER NoVoice
  Skip audio entirely (text-only chat).

.PARAMETER Voice
  Talk to the robot with your voice instead of typing (STT=1). Opens a
  push-to-talk listener window (scripts\listen.ps1) that captures your mic and
  feeds the transcript to the robot. Requires faster-whisper + sounddevice on
  Windows: pip install faster-whisper sounddevice numpy

.PARAMETER Camera
  Also send the current camera frame each turn (CHAT_USE_CAMERA=1).

.PARAMETER CameraSource
  Where camera frames come from (sets CAMERA_SOURCE). Examples:
  a phone IP-Webcam snapshot "http://<phone-ip>:8080/shot.jpg", "synthetic",
  "webcam:0", or "dir:/workspace/python_control/frames". Implies -Camera.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\chat.ps1

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\chat.ps1 -CameraSource http://192.168.4.127:8080/shot.jpg
#>
param(
    [switch]$NoVoice,
    [switch]$Voice,
    [switch]$Camera,
    [string]$CameraSource
)

$repo = Split-Path $PSScriptRoot -Parent
$ttsDir = Join-Path $repo "tts_out"
$sttDir = Join-Path $repo "stt_in"

$player = $null
if (-not $NoVoice) {
    New-Item -ItemType Directory -Force -Path $ttsDir | Out-Null
    Get-ChildItem "$ttsDir\*.wav" -ErrorAction SilentlyContinue | Remove-Item -ErrorAction SilentlyContinue
    Write-Host "Starting voice player (minimized)..."
    $player = Start-Process powershell -PassThru -WindowStyle Minimized -ArgumentList @(
        "-ExecutionPolicy", "Bypass", "-File", (Join-Path $PSScriptRoot "play_tts.ps1")
    )
}

# The push-to-talk listener needs its own visible window (you press Enter there
# to start/stop each recording), unlike the fire-and-forget player.
$listener = $null
if ($Voice) {
    New-Item -ItemType Directory -Force -Path $sttDir | Out-Null
    Get-ChildItem "$sttDir\*.txt" -ErrorAction SilentlyContinue | Remove-Item -ErrorAction SilentlyContinue
    Write-Host "Starting voice listener (push-to-talk window)..."
    $listener = Start-Process powershell -PassThru -ArgumentList @(
        "-ExecutionPolicy", "Bypass", "-File", (Join-Path $PSScriptRoot "listen.ps1")
    )
}

$envs = "INPUT_MODE=chat"
if (-not $NoVoice) { $envs += " TTS=1" }
if ($Voice) { $envs += " STT=1" }
if ($Camera -or $CameraSource) { $envs += " CHAT_USE_CAMERA=1" }
if ($CameraSource) { $envs += " CAMERA_SOURCE=$CameraSource" }

Write-Host "Opening chat. Type 'quit' or Ctrl+C to exit.`n"
try {
    docker exec -it -u root reachy-sim bash -lc "cd /workspace/python_control && $envs python control_script.py"
}
finally {
    if ($player -and -not $player.HasExited) {
        Write-Host "`nStopping voice player..."
        Stop-Process -Id $player.Id -ErrorAction SilentlyContinue
    }
    if ($listener -and -not $listener.HasExited) {
        Write-Host "Stopping voice listener..."
        Stop-Process -Id $listener.Id -ErrorAction SilentlyContinue
    }
}
