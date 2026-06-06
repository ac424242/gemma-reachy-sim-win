# Setup & usage details

Full guide for [Virtual Reachy Mini](README.md): architecture, manual setup,
camera bridges, voice chat, and troubleshooting.

## Architecture

- **Ollama container** (`docker-compose.yml`) serves `gemma3:4b` on `:11434`.
- **lerobot-gpu container** runs the Reachy Mini sim daemon + the control loop.
- **VcXsrv** (Windows X server) displays the MuJoCo window from the container.
- Inside the container, reach Ollama at `host.docker.internal:11434` (not
  `localhost`).

## One-click start (Windows)

Double-click **`StartReachy.bat`** at the repo root (or run the script below).
It attempts to start everything: Docker Desktop, VcXsrv, Ollama + `gemma3:4b`,
the `reachy-sim` container (first-run setup included), the MuJoCo sim window
(brought to the front), and the chat control loop (`STT=1`, Piper replies in
the browser). When startup finishes it **prompts you** to open the voice page
at **[http://localhost:7860](http://localhost:7860)** (you can confirm to launch the browser or open it
yourself).

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start_robot.ps1
```

With a phone camera each chat turn:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start_robot.ps1 `
  -CameraSource http://192.168.4.159:8080/shot.jpg
```

Stop everything with **`StopReachy.bat`** or `scripts\stop_robot.ps1`. Pin
`StartReachy.bat` to the desktop or taskbar for a permanent launcher.

Prerequisites still apply (Docker Desktop, VcXsrv, NVIDIA GPU, images pulled).
First run can take several minutes (Reachy stack install + voice image build).

## Prerequisites

- Windows with an NVIDIA GPU (validated on RTX 5070 Laptop, 8 GB).
- Docker Desktop with WSL2 + NVIDIA GPU integration.
- [VcXsrv](https://sourceforge.net/projects/vcxsrv/) installed.
- The `huggingface/lerobot-gpu` image pulled: `docker pull huggingface/lerobot-gpu`.

## 1. Start Gemma 3 (Ollama)

```powershell
docker compose up -d
docker exec -it ollama ollama pull gemma3:4b   # vision-capable, fits 8 GB
```

Sanity check (optional): `python scripts/verify_prereqs.py` (run on the host;
checks Ollama reachability, vision, JSON output, and the lerobot-gpu image).

## 2. Start the X server (VcXsrv)

Run **XLaunch** → Multiple windows → Start no client → **check "Disable access
control"** → Finish. Or start it directly:

```powershell
& "C:\Program Files\VcXsrv\vcxsrv.exe" :0 -multiwindow -clipboard -wgl -ac
```

Allow VcXsrv through Windows Firewall on **Private** networks if prompted.

## 3. Launch the container

```powershell
./scripts/run_container.ps1
```

This starts a persistent container named `reachy-sim` (as root, GPU on,
`DISPLAY=host.docker.internal:0.0`, repo mounted at `/workspace`).

## 4. One-time setup inside the container

```bash
bash /workspace/scripts/container_setup.sh
```

Installs everything the Reachy stack needs (GL/X libs, GStreamer typelibs, the
`pygobject` build fix, and `reachy-mini[mujoco]` + loop deps). Idempotent.

## 5. Run the sim + control loop

The daemon and the loop are two processes. Use the container shell from step 3
for the daemon, and open a second shell with `docker exec`.

**Terminal 1 - the simulator (MuJoCo window appears on Windows):**

```bash
DISPLAY=host.docker.internal:0.0 reachy-mini-daemon --sim
```

**Terminal 2 - the Gemma control loop:**

```powershell
docker exec -u root reachy-sim bash -lc "cd /workspace/python_control && \
  OLLAMA_HOST=http://host.docker.internal:11434 python control_script.py"
```

Watch the Reachy Mini head + antennas react to Gemma's chosen expression and the
wheel commands print in the loop log. Stop with `Ctrl+C`.

### Useful environment variables (control loop)

| Variable | Default | Meaning |
|---|---|---|
| `OLLAMA_HOST` | `http://host.docker.internal:11434` | Ollama endpoint |
| `GEMMA_MODEL` | `gemma3:4b` | model tag |
| `LOOP_INTERVAL_SEC` | `3.0` | seconds between iterations |
| `MAX_ITERS` | `0` | `0` = run until Ctrl+C; set e.g. `5` for a short demo |
| `CAMERA_SOURCE` | `auto` | where frames come from (see below) |
| `INPUT_MODE` | `camera` | `camera` = autonomous vision loop; `chat` = type to it |
| `CHAT_USE_CAMERA` | `0` | in chat mode, also send the camera frame each turn |
| `LLM_TEMPERATURE` | `0.8` | sampling temperature; higher = livelier/more varied, `0` = deterministic |
| `TTS` | `0` | `1` to speak each chat reply aloud (writes WAVs for the host player) |
| `TTS_DIR` | `/workspace/tts_out` | folder for synthesized clips (bind-mounted to `tts_out/`) |
| `TTS_VOICE` | `en+f3` | espeak-ng voice (cartoon pixie; try `en+f5`, `en+m7`) |
| `TTS_PITCH` | `88` | espeak-ng pitch 0-99; higher = squeakier/more cartoonish |
| `TTS_RATE` | `175` | speech rate in words/min |
| `TTS_GAP` | `4` | pause between words (10ms units) for a clipped cartoon rhythm |
| `STT` | `0` | `1` to take chat turns from your voice instead of typing (chat mode; see "Talk to it with your voice") |
| `STT_DIR` | `/workspace/stt_in` | folder watched for mic transcripts (bind-mounted to `stt_in/`) |
| `STT_POLL_SEC` | `0.25` | how often (seconds) the loop checks for a new transcript |
| `REPLY_DIR` | *(empty)* | when set (e.g. `/workspace/replies`), write each chat reply here for the voice container to Piper-TTS back to the browser |

### Using a real camera (CAMERA_SOURCE)

By default the loop uses synthetic test frames, so Gemma mostly picks
`neutral`/`stop`. Point it at a real image and it reacts. `CAMERA_SOURCE` accepts:

| Value | Frame source |
|---|---|
| `auto` | Reachy Mini camera if available, else synthetic (default) |
| `synthetic` | generated test pattern |
| `webcam` / `webcam:N` | local OpenCV camera index `N` |
| `http://host/shot.jpg` | fetch a JPEG snapshot each iteration |
| `dir:/path` | newest image file in a directory |
| `file:/path` | a single image file |

**Important:** a Windows USB webcam is **not** visible inside the Linux
container (`webcam:0` won't work there). Use one of these bridges instead:

- **Phone as camera** (easiest, validated) — see the walkthrough below.
- **Snapshot/MJPEG server on Windows**: expose a `/shot.jpg` endpoint and use
  `CAMERA_SOURCE=http://host.docker.internal:<port>/shot.jpg`.
- **Drop-folder**: have any capture tool write JPEGs into a bind-mounted folder
  (e.g. `python_control/frames/`) and use
  `CAMERA_SOURCE=dir:/workspace/python_control/frames`.
- **Native run** (not in this container): `webcam:0` works directly.

Quick reaction test against live photos (validates the URL path):

```bash
cd /workspace/python_control
CAMERA_SOURCE=https://picsum.photos/320/240 LOOP_INTERVAL_SEC=1 python control_script.py
```

### Connect your phone camera (validated)

The PC and phone bridge over your local Wi-Fi; the container fetches a JPEG
snapshot from the phone each iteration.

1. Phone and PC on the **same Wi-Fi** (a non-guest / "Private" network).
2. Install an **"IP Webcam"** app (Android) and tap **Start server**. It shows a
   URL like `http://192.168.4.89:8080`; the snapshot endpoint is `…/shot.jpg`.
3. **Remove the app's login/password** (clear the Login/password fields in the
   app settings). With a password set, the snapshot returns `HTTP 401` and the
   loop can't read it (Python's `urllib` doesn't send `user:pass@` credentials).
4. Verify the container can reach it (expect `HTTP=200` and non-zero bytes):

```powershell
docker exec reachy-sim bash -lc "curl -s -m 8 -o /tmp/shot.jpg -w 'HTTP=%{http_code} bytes=%{size_download}\n' http://<phone-ip>:8080/shot.jpg"
```

5. Make sure `reachy-mini-daemon --sim` is running, then drive the robot off the
   phone (runs until Ctrl+C):

```powershell
docker exec -u root reachy-sim bash -lc "cd /workspace/python_control && \
  CAMERA_SOURCE=http://<phone-ip>:8080/shot.jpg \
  OLLAMA_HOST=http://host.docker.internal:11434 \
  LOOP_INTERVAL_SEC=2 python control_script.py"
```

Tips: use the `/shot.jpg` snapshot (not the `/video` MJPEG stream); keep the app
in the foreground with the screen on; the phone IP can change on reconnect (a
DHCP reservation pins it).

**Use `http://`, not `https://` (validated quirk):** the IP Webcam app may also
expose an `https` endpoint, but the control loop fetches frames with Python's
`urllib`, which rejects the app's self-signed TLS certificate and the fetch
fails. Plain `http://<phone-ip>:8080/shot.jpg` serves the identical snapshot and
just works. (`curl` only succeeds against the `https` endpoint because `-k`
skips verification — the Python path has no such bypass.) A live source is
confirmed working when repeated fetches return `HTTP=200` with a *changing* byte
count each time.

## Talk to it (and hear it talk back)

In chat mode you type, the robot answers a real question/response, emotes (head
+ antennas), moves, and speaks the reply out loud.

### Easiest: one command (with voice)

`scripts\chat.ps1` starts the speaker player for you (minimized), opens the
chat, and stops the player when you quit — no second window to manage:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\chat.ps1
```

Options: `-NoVoice` (text only), `-Camera` (also send the camera frame each
turn), `-CameraSource <spec>` (send a live frame from a specific source each
turn, e.g. a phone snapshot URL — implies `-Camera`; see "Using a real camera"
for valid specs). Requires the `reachy-sim` container running; start the sim too
(`reachy-mini-daemon --sim`) to see the head/antennas react.

```powershell
# chat with voice + a live phone-camera frame each turn
powershell -ExecutionPolicy Bypass -File scripts\chat.ps1 -CameraSource http://<phone-ip>:8080/shot.jpg
```

Each turn prints: `robot> <spoken answer>  [<expression> / <movement>]`.

### Manual: two windows

Why two? The container has **no sound card**, so it can't play audio itself.
With `TTS=1` it writes each reply (espeak-ng) as a `.wav` into `tts_out/` (a
folder shared with Windows); a small host-side watcher plays new clips on your
speakers. So one side *writes the file*, the other *plays it* — both must run.

1. In a **separate PowerShell window**, start the player and leave it open:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\play_tts.ps1
```

2. In your main window, start chat with TTS on:

```powershell
docker exec -it -u root reachy-sim bash -lc "cd /workspace/python_control && INPUT_MODE=chat TTS=1 python control_script.py"
```

(Drop `TTS=1` for text-only; add `CHAT_USE_CAMERA=1` so it can also answer about
what it sees.)

If you hear nothing, the player window almost certainly isn't running — clips
will just pile up in `tts_out/`.

The voice is a cartoonish pixie by default. Tune it with `TTS_VOICE` (e.g.
`en+f5` chipmunk, `en+m7` goofy), `TTS_PITCH` (0-99, higher = squeakier), and
`TTS_RATE` (words/min). `espeak-ng` is installed by `container_setup.sh`.

### Talk to it with your voice (speech-to-text)

You can speak your chat turns instead of typing them. Just like the container
has **no sound card** (so TTS writes WAVs for a host player), it has **no
microphone** - so the mic is captured on Windows and the transcript is handed to
the container through the bind-mounted `stt_in/` folder, the exact input mirror
of `tts_out/`. The robot's brain is unchanged; only the source of your message
text changes from the keyboard to your voice.

Transcription runs on Windows with `faster-whisper` (GPU-accelerated on your
RTX 5070). Install the host deps once:

```powershell
pip install faster-whisper sounddevice numpy
```

#### Easiest: one command (voice in, voice out)

```powershell
powershell -ExecutionPolicy Bypass -File scripts\chat.ps1 -Voice
```

This opens a push-to-talk **listener window** alongside the chat: press Enter
there, speak, press Enter again, and the robot answers out loud. Combine with
`-CameraSource` to also send a live frame each turn. (`-Voice` is separate from
`-NoVoice`, which controls whether the robot *speaks back*.)

#### Manual: separate windows

The listener is the input twin of `play_tts.ps1`. In its own PowerShell window:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\listen.ps1
```

Then start chat with `STT=1` so the loop reads transcripts instead of stdin:

```powershell
docker exec -it -u root reachy-sim bash -lc "cd /workspace/python_control && INPUT_MODE=chat STT=1 TTS=1 python control_script.py"
```

It's push-to-talk by design (press Enter to start/stop each recording) so it
won't transcribe the robot's own spoken replies or background noise. Tune the
model size with `listen.ps1 -Model` (`tiny`/`base`/`small`/`medium`/`large-v3`;
default `small`).

### Browser voice (containerized — no host Python)

A dedicated **voice** container handles mic capture and speech playback through
your browser. The container has no microphone or speakers (same as
`reachy-sim`), so the browser is the audio endpoint: it records with
`getUserMedia`, posts audio to the voice service, and plays the Piper reply.
Transcripts and answers cross to the robot through shared `stt_in/` and
`replies/` folders — no `listen.ps1`, `play_tts.ps1`, or host Python needed.

```mermaid
flowchart LR
    browser[Browser mic + speaker] --> voice[voice container]
    voice --> stt[stt_in]
    stt --> loop[control_script chat STT=1]
    loop --> rep[replies]
    rep --> voice
    loop --> gemma[Gemma + sim]
```

1. Build and start the voice service (from the repo root):

```powershell
docker compose up -d --build voice
```

2. Start the chat loop in `reachy-sim` (sim daemon must already be running):

```powershell
docker exec -it -u root reachy-sim bash -lc "cd /workspace/python_control && \
  INPUT_MODE=chat STT=1 TTS=0 STT_DIR=/workspace/stt_in REPLY_DIR=/workspace/replies \
  python control_script.py"
```

(`TTS=0` keeps espeak silent; Piper in the browser speaks instead. Add
`CHAT_USE_CAMERA=1` and `CAMERA_SOURCE=…` to also send a live frame each turn.)

3. Open **[http://localhost:7860](http://localhost:7860)**, allow the microphone, hold **Hold to talk**,
speak, release. The robot answers in the page and reacts in MuJoCo.

The host-side `listen.ps1` / `chat.ps1 -Voice` path still works; pick one STT
source per session (don't run both listeners at once).

## Files

| Path | Purpose |
|---|---|
| `docker-compose.yml` | Ollama + Open WebUI (GPU) + voice (browser STT/TTS) |
| `voice/server.py` | browser voice bridge: Whisper STT + Piper TTS + file handoff |
| `voice/static/index.html` | push-to-talk web UI (mic capture + playback) |
| `python_control/control_script.py` | main loop: camera/chat -> Gemma -> dispatch (+TTS) |
| `scripts/chat.ps1` | one-command chat with voice (auto-starts the player) |
| `scripts/play_tts.ps1` | Windows watcher that plays the robot's spoken replies |
| `scripts/listen.ps1` | Windows push-to-talk mic listener (writes transcripts to `stt_in/`) |
| `scripts/transcribe.py` | host helper: records the mic and transcribes it with faster-whisper |
| `python_control/expressions.py` | expression -> head/antenna keyframes |
| `python_control/wheel_controller.py` | simulated wheel motor for `movement` |
| `StartReachy.bat` | double-click launcher: full stack + browser voice UI |
| `StopReachy.bat` | double-click launcher: tear down reachy-sim + compose |
| `scripts/start_robot.ps1` | one-click startup orchestrator (used by StartReachy.bat) |
| `scripts/stop_robot.ps1` | stop in-container processes, remove reachy-sim, compose down |
| `scripts/run_container.ps1` | launch the container with X + GPU (interactive shell) |
| `scripts/container_setup.sh` | one-time in-container dependency install |
| `scripts/verify_prereqs.py` | check Ollama/vision/JSON/image/Python |
| `scripts/mujoco_xtest.py` | minimal MuJoCo viewer X-forwarding test |
| `requirements.txt` | Python deps (see note - use container_setup.sh) |

## Troubleshooting

- **MuJoCo window is black or `GLXBadDrawable`**: VcXsrv's hardware GLX is
  flaky. Re-run XLaunch with **Native opengl unchecked**, or in the container
  `export LIBGL_ALWAYS_SOFTWARE=1` before starting the daemon. A clean X test:
  `DISPLAY=host.docker.internal:0.0 python /workspace/scripts/mujoco_xtest.py`.
- **No window / "cannot open display"**: VcXsrv not running, "Disable access
  control" not checked, or firewall blocking it. Verify with `xeyes`.
- **Control loop logs "Running in dry-run mode"**: the daemon isn't running, or
  the SDK couldn't connect. Make sure `reachy-mini-daemon --sim` is up first.
  The loop already uses `media_backend="no_media"` to avoid the daemon's
  WebRTC camera dependency.
- **`reachy-mini` install fails on pygobject / `girepository-1.0`**: use
  `container_setup.sh` (it aliases the pkg-config file and installs build deps).
  This is an Ubuntu 24.04 quirk.
- **`apt-get` Permission denied**: the image's default user isn't root; run the
  container or `docker exec` with `-u root`.
- **Ollama unreachable from the container**: use `host.docker.internal:11434`,
  not `localhost`. Fallback: your Windows IPv4.
- **Gemma keeps choosing `neutral`/`stop`**: the synthetic test frames are
  static. Set `CAMERA_SOURCE` to a real image/webcam/URL (see "Using a real
  camera") to get richer reactions.
- **Don't re-run `container_setup.sh` every time**: the installed Reachy stack
  persists in the container's filesystem. If `reachy-sim` already exists, just
  `docker start reachy-sim` and reuse it — setup is only needed once per
  container (re-running `run_container.ps1` with the same name fails because the
  name is taken; `docker rm -f reachy-sim` first if you really want a fresh one).
- **Harmless daemon startup errors**: `Failed to initialize media server ...
  webrtcsink` and `No USB backend was found` appear when starting
  `reachy-mini-daemon --sim` but are expected and safe to ignore — the control
  loop uses `media_backend="no_media"` and supplies its own frames. Look for
  `Daemon started successfully` to confirm the sim is up.
- **Run the daemon in the background**: instead of a dedicated terminal you can
  detach it with `docker exec -d`, logging to a file:
  `docker exec -d -u root reachy-sim bash -lc "DISPLAY=host.docker.internal:0.0 reachy-mini-daemon --sim > /tmp/daemon.log 2>&1"`
  (add `LIBGL_ALWAYS_SOFTWARE=1` if the MuJoCo window is black/`GLXBadDrawable`).

## Spin down (stop everything)

Tear it all down in roughly the reverse order you started it. Each step is safe
to run even if that piece is already stopped.

```powershell
# 1. Stop the in-container loop + daemon, then remove the container
#    (removing the container also kills anything running inside it)
docker exec -u root reachy-sim bash -lc "pkill -f control_script.py; pkill -f reachy-mini-daemon" 2>$null
docker rm -f reachy-sim

# 2. Stop Ollama + Open WebUI (run from the repo root)
docker compose down

# 3. Stop the voice player and any chat windows
Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" |
  Where-Object { $_.CommandLine -match 'play_tts\.ps1|chat\.ps1' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

# 4. Stop the X server
Get-Process vcxsrv -ErrorAction SilentlyContinue | Stop-Process -Force
```

Verify nothing is left running (expect no `reachy-sim`/`ollama`, no `vcxsrv`,
and no listeners on `11434` or `6000`):

```powershell
docker ps
Get-Process vcxsrv -ErrorAction SilentlyContinue
Get-NetTCPConnection -LocalPort 11434,6000 -ErrorAction SilentlyContinue
```

Notes:

- If you launched `chat.ps1`, quitting the chat (`quit` / Ctrl+C) already stops
  its voice player automatically — step 3 is a backstop.
- `docker compose down` keeps the `ollama_data` / `open-webui_data` volumes, so
  your pulled models survive. Add `-v` only if you want to delete them too.
- The container **image** and any stopped containers are kept on disk for a fast
  restart; `docker rm -f reachy-sim` only removes the running sim container.

## Notes

- `movement` is simulated only (Reachy Mini has no wheels); the wheel controller
  tracks virtual odometry and logs intent, and optionally nudges head yaw on
  turns so the motion is visible.
- Stop everything when done: see "Spin down" above (quick version:
  `docker rm -f reachy-sim` + `docker compose down`).
