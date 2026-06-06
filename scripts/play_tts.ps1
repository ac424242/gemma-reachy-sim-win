<#
.SYNOPSIS
  Play the robot's spoken replies on your Windows speakers.

.DESCRIPTION
  The control script runs inside the Linux container, which has no sound card,
  so it writes each reply as a .wav into the bind-mounted tts_out/ folder. This
  watcher plays new clips (oldest first) through your default audio device and
  deletes them afterwards.

  Run this in its own PowerShell window while you chat with the robot:
      powershell -ExecutionPolicy Bypass -File scripts\play_tts.ps1

.PARAMETER Dir
  Folder to watch. Defaults to ..\tts_out relative to this script (the same
  path the container sees as /workspace/tts_out).
#>
param(
    [string]$Dir = (Join-Path $PSScriptRoot "..\tts_out")
)

$ErrorActionPreference = "SilentlyContinue"
New-Item -ItemType Directory -Force -Path $Dir | Out-Null
$Dir = (Resolve-Path $Dir).Path

Write-Host "Listening for robot speech in: $Dir"
Write-Host "Leave this window open while you chat. Ctrl+C to stop."

$player = New-Object System.Media.SoundPlayer
while ($true) {
    $files = Get-ChildItem -Path $Dir -Filter *.wav | Sort-Object Name
    foreach ($f in $files) {
        try {
            $player.SoundLocation = $f.FullName
            $player.PlaySync()
        } catch {
            Write-Host "Could not play $($f.Name): $_"
        }
        Remove-Item $f.FullName
    }
    Start-Sleep -Milliseconds 250
}
