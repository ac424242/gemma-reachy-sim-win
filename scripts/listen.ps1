<#
.SYNOPSIS
  Talk to the robot with your voice (push-to-talk speech-to-text).

.DESCRIPTION
  The control script runs inside the Linux container, which has no microphone,
  so this host-side listener captures your mic and transcribes it. Press Enter
  to start recording, speak, then press Enter again to stop; the recognized
  text is written as a .txt into the bind-mounted stt_in/ folder, which the
  container reads as your chat turn (run the loop with STT=1).

  The input twin of scripts\play_tts.ps1. Run it in its own PowerShell window
  while you chat with the robot:
      powershell -ExecutionPolicy Bypass -File scripts\listen.ps1

  Requires faster-whisper + sounddevice on Windows:
      pip install faster-whisper sounddevice numpy

.PARAMETER Dir
  Folder to write transcripts into. Defaults to ..\stt_in relative to this
  script (the same path the container sees as /workspace/stt_in).

.PARAMETER Model
  faster-whisper model size (tiny/base/small/medium/large-v3). Default: small.
#>
param(
    [string]$Dir = (Join-Path $PSScriptRoot "..\stt_in"),
    [string]$Model = "small"
)

$ErrorActionPreference = "SilentlyContinue"
New-Item -ItemType Directory -Force -Path $Dir | Out-Null
$Dir = (Resolve-Path $Dir).Path
$transcribe = Join-Path $PSScriptRoot "transcribe.py"

Write-Host "Voice input -> $Dir"
Write-Host "Leave this window open while you chat. Ctrl+C to stop.`n"

while ($true) {
    Read-Host "Press Enter to start recording" | Out-Null
    # transcribe.py records until you press Enter again, then prints the text.
    $text = (& python $transcribe --model $Model | Out-String).Trim()
    if (-not $text) {
        Write-Host "(nothing recognized)"
        continue
    }
    Write-Host "you> $text"
    # Atomic write: .part first, then rename, so the container never reads a
    # half-written transcript (matches the WAV convention on the TTS side).
    $ts = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
    $tmp = Join-Path $Dir "$ts.txt.part"
    $final = Join-Path $Dir "$ts.txt"
    Set-Content -Path $tmp -Value $text -Encoding utf8 -NoNewline
    Move-Item -Path $tmp -Destination $final
}
