"""Virtual Reachy Mini control loop driven by Gemma 3.

Pipeline (runs inside the lerobot-gpu container, MuJoCo GUI forwarded via VcXsrv):

    virtual camera frame
        -> Gemma 3 (vision) in Ollama, forced to emit strict JSON
        -> {"expression": ..., "movement": ...}
        -> expression  : ReachyMini head + antennas  (expressions.apply_expression)
        -> movement     : simulated WheelController     (wheel_controller.WheelController)

Both actuation paths run together each loop. The script degrades gracefully: if
the ReachyMini SDK/sim is not connected it runs in dry-run mode (logging the
gestures), and if no camera is available it falls back to a synthetic frame, so
the end-to-end logic is always exercisable.

Config via environment variables:
    OLLAMA_HOST        default http://host.docker.internal:11434
    GEMMA_MODEL        default gemma3:4b
    LOOP_INTERVAL_SEC  default 3.0
    MAX_ITERS          default 0 (run until Ctrl+C)
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from typing import Optional

import numpy as np

try:
    import cv2
except Exception:  # pragma: no cover - cv2 missing only outside the container
    cv2 = None

import ollama

from expressions import VALID_EXPRESSIONS, apply_expression
from wheel_controller import VALID_MOVEMENTS, WheelController

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-11s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("control")

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://host.docker.internal:11434")
GEMMA_MODEL = os.environ.get("GEMMA_MODEL", "gemma3:4b")
LOOP_INTERVAL_SEC = float(os.environ.get("LOOP_INTERVAL_SEC", "3.0"))
MAX_ITERS = int(os.environ.get("MAX_ITERS", "0"))
# Where camera frames come from. One of:
#   auto              - Reachy Mini camera if available, else synthetic (default)
#   synthetic         - generated test pattern
#   webcam | webcam:N - local OpenCV camera index N (needs a video device; on
#                       Windows+Docker a USB cam is NOT visible in the container)
#   http://host/shot.jpg - fetch a JPEG snapshot each iteration (phone/IP-cam app)
#   dir:/path         - newest image file in a (bind-mounted) directory
#   file:/path        - a single image file
CAMERA_SOURCE = os.environ.get("CAMERA_SOURCE", "auto")
# "camera" (default): autonomous loop reacting to frames.
# "chat": interactive REPL - you type messages and the robot reacts + replies.
INPUT_MODE = os.environ.get("INPUT_MODE", "camera").strip().lower()
# In chat mode, also send the current camera frame to Gemma (multimodal chat).
CHAT_USE_CAMERA = os.environ.get("CHAT_USE_CAMERA", "0").lower() in ("1", "true", "yes")
# Sampling temperature. Higher = livelier / more varied choices. Structured
# output keeps the JSON valid at any temperature.
LLM_TEMPERATURE = float(os.environ.get("LLM_TEMPERATURE", "0.8"))
# Text-to-speech: speak each chat reply out loud. The container has no audio
# device, so we synthesize a WAV with espeak-ng into TTS_DIR (a bind-mounted
# folder); run scripts/play_tts.ps1 on Windows to hear new clips. "1" to enable.
TTS_ENABLED = os.environ.get("TTS", "0").lower() in ("1", "true", "yes", "on")
TTS_DIR = os.environ.get("TTS_DIR", "/workspace/tts_out")
TTS_RATE = int(os.environ.get("TTS_RATE", "175"))  # words/min
TTS_VOICE = os.environ.get("TTS_VOICE", "en+f3")  # espeak-ng voice (cartoon pixie)
TTS_PITCH = int(os.environ.get("TTS_PITCH", "88"))  # 0-99, higher = squeakier
TTS_GAP = int(os.environ.get("TTS_GAP", "4"))  # pause between words (10ms units)
# Speech-to-text: read each chat turn from a transcript dropped by a host-side
# mic listener. The container has no microphone (same reason TTS writes WAVs out
# for a host player), so scripts/listen.ps1 captures + transcribes the mic on
# Windows and writes <timestamp>.txt into STT_DIR (a bind-mounted folder). "1"
# to enable; only meaningful in chat mode.
STT_ENABLED = os.environ.get("STT", "0").lower() in ("1", "true", "yes", "on")
STT_DIR = os.environ.get("STT_DIR", "/workspace/stt_in")
STT_POLL_SEC = float(os.environ.get("STT_POLL_SEC", "0.25"))
# Reply sink for the browser voice container: when set, each chat reply is also
# written as <timestamp>.txt into this folder so voice/server.py can Piper-TTS it
# back to the browser. Leave empty for host-side TTS (tts_out/) only.
REPLY_DIR = os.environ.get("REPLY_DIR", "").strip()

# Strict structured-output schema. Ollama uses this to constrain Gemma's output
# so message.content is guaranteed-parseable JSON with exactly these fields.
SCHEMA = {
    "type": "object",
    "properties": {
        "expression": {"type": "string", "enum": VALID_EXPRESSIONS},
        "movement": {"type": "string", "enum": VALID_MOVEMENTS},
    },
    "required": ["expression", "movement"],
}

# Chat mode adds a free-text "reply" so the robot answers while it acts.
CHAT_SCHEMA = {
    "type": "object",
    "properties": {
        "expression": {"type": "string", "enum": VALID_EXPRESSIONS},
        "movement": {"type": "string", "enum": VALID_MOVEMENTS},
        "reply": {"type": "string"},
    },
    "required": ["expression", "movement", "reply"],
}

PROMPT = (
    "You are the lively perception-and-decision module of a small desktop robot. "
    "Look at the camera frame and react expressively, like a curious, playful pet. "
    "Choose one facial expression and one movement command that genuinely fit what "
    "you see, and match the emotion (bright/fun scene -> happy; new or interesting "
    "thing -> curious; dull/empty -> sad; something annoying -> angry). "
    "Keep moving and exploring: prefer turning or stepping to look around, and only "
    "use 'stop' when staying put truly makes sense. Vary your choices - avoid "
    "repeating the same expression and movement every time. "
    f"expression must be one of {VALID_EXPRESSIONS}. "
    f"movement must be one of {VALID_MOVEMENTS}. "
    "Respond with JSON only."
)

CHAT_SYSTEM_PROMPT = (
    "You are a small, lively, playful desktop robot (Reachy Mini) that the user "
    "talks to. You have a big personality, but you are also genuinely helpful. "
    "For every user message: actually answer their question or respond to what "
    "they said, clearly and accurately, in your own upbeat robot voice. If they "
    "ask a factual question, give the real answer. "
    "At the same time, react physically: match your 'expression' to the mood "
    "(happy when things are good, curious when intrigued or asked something "
    "interesting, sad when let down, angry when teased), and pick a 'movement' "
    "that fits - prefer an active one (turn, step forward/back) and only use "
    "'stop' when holding still really fits. Keep expressions and movements varied "
    "rather than repetitive. "
    f"'expression' must be one of {VALID_EXPRESSIONS}. "
    f"'movement' must be one of {VALID_MOVEMENTS}. "
    "'reply' is your spoken answer: usually one to three sentences - short for "
    "small talk, longer when a real question needs a real explanation. "
    "Always respond with JSON only matching the required schema."
)


class GracefulExit:
    """Flips to True on SIGINT/SIGTERM so the loop can shut down cleanly."""

    def __init__(self) -> None:
        self.stop = False
        signal.signal(signal.SIGINT, self._handle)
        try:
            signal.signal(signal.SIGTERM, self._handle)
        except (ValueError, AttributeError):  # SIGTERM not always available on Windows
            pass

    def _handle(self, *_args) -> None:
        logger.info("Shutdown signal received, finishing current step...")
        self.stop = True


def connect_reachy():
    """Return a connected ReachyMini context manager, or None for dry-run mode.

    media_backend="no_media" skips the daemon's camera/WebRTC media setup (which
    needs the GStreamer webrtc plugin and isn't required here - the loop supplies
    its own frames). Without this, construction fails on the media connection
    even though head/antenna control would work fine.
    """
    try:
        from reachy_mini import ReachyMini

        mini = ReachyMini(media_backend="no_media")
        logger.info("Connected to Reachy Mini (sim or hardware).")
        return mini
    except Exception as exc:
        logger.warning("Reachy Mini not available (%s). Running in dry-run mode.", exc)
        return None


def _synthetic_frame(counter: int) -> np.ndarray:
    """Build a deterministic-ish RGB frame so the loop works with no camera."""
    h, w = 240, 320
    frame = np.zeros((h, w, 3), dtype=np.uint8)
    # moving gradient + a shape so the model has something to describe
    shift = (counter * 12) % 255
    frame[:, :, 0] = (np.linspace(0, 255, w, dtype=np.uint8) + shift) % 255
    frame[:, :, 1] = np.linspace(0, 255, h, dtype=np.uint8)[:, None]
    cx = int((counter * 20) % w)
    if cv2 is not None:
        cv2.circle(frame, (cx, h // 2), 30, (255, 255, 255), -1)
        cv2.putText(frame, f"frame {counter}", (8, 24), cv2.FONT_HERSHEY_SIMPLEX,
                    0.6, (255, 255, 255), 2)
    return frame


def _encode_jpeg(frame: np.ndarray) -> bytes:
    """Encode an image array (BGR, as OpenCV produces) to JPEG bytes."""
    frame = np.ascontiguousarray(frame).astype(np.uint8)
    if cv2 is not None:
        ok, buf = cv2.imencode(".jpg", frame)
        if ok:
            return buf.tobytes()
    # Last-resort: hand Ollama the raw bytes (still valid for the capability test).
    return frame.tobytes()


_TTS_ENGINE: Optional[str] = None
_TTS_WARNED = False


def speak(text: str) -> None:
    """Synthesize `text` to a WAV in TTS_DIR so a host-side player can voice it.

    No-op unless TTS is enabled. The container has no sound card, so we can't
    play audio directly; instead we drop a timestamped .wav into a bind-mounted
    folder and let scripts/play_tts.ps1 on Windows play it. We write to a
    .part file first and atomically rename, so the watcher never reads a
    half-written clip.
    """
    global _TTS_ENGINE, _TTS_WARNED
    if not TTS_ENABLED or not text.strip():
        return
    if _TTS_ENGINE is None:
        _TTS_ENGINE = shutil.which("espeak-ng") or shutil.which("espeak") or ""
    if not _TTS_ENGINE:
        if not _TTS_WARNED:
            logger.warning("TTS on but espeak-ng not installed; run container_setup.sh")
            _TTS_WARNED = True
        return
    try:
        os.makedirs(TTS_DIR, exist_ok=True)
        final = os.path.join(TTS_DIR, f"{int(time.time() * 1000)}.wav")
        tmp = final + ".part"
        subprocess.run(
            [
                _TTS_ENGINE,
                "-v", TTS_VOICE,
                "-s", str(TTS_RATE),
                "-p", str(TTS_PITCH),
                "-g", str(TTS_GAP),
                "-w", tmp,
                text,
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        os.replace(tmp, final)
    except Exception as exc:  # pragma: no cover - audio is best-effort
        logger.warning("TTS failed: %s", exc)


def reply_sink(text: str) -> None:
    """Drop the chat reply into REPLY_DIR for the voice container to speak.

    No-op unless REPLY_DIR is set. Uses the same atomic .part -> rename pattern
    as speak() and listen_text() so the voice server never reads a half-written
    file.
    """
    if not REPLY_DIR or not text.strip():
        return
    try:
        os.makedirs(REPLY_DIR, exist_ok=True)
        final = os.path.join(REPLY_DIR, f"{int(time.time() * 1000)}.txt")
        tmp = final + ".part"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, final)
    except OSError as exc:
        logger.warning("Reply sink failed (%s): %s", REPLY_DIR, exc)


def listen_text(life: "GracefulExit") -> Optional[str]:
    """Block until the host listener drops a transcript in STT_DIR; return it.

    The input mirror of speak(): the container has no microphone, so
    scripts/listen.ps1 captures + transcribes the mic on Windows and writes a
    <timestamp>.txt here (atomically, via a .part rename), and we consume the
    oldest one. Returns the recognized text, or None if we were asked to shut
    down while waiting (so Ctrl+C stays responsive).
    """
    os.makedirs(STT_DIR, exist_ok=True)
    while not life.stop:
        try:
            files = sorted(
                f for f in os.listdir(STT_DIR)
                if f.endswith(".txt") and not f.endswith(".part")
            )
        except OSError as exc:
            logger.warning("STT dir read failed (%s): %s", STT_DIR, exc)
            files = []
        if files:
            path = os.path.join(STT_DIR, files[0])
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    text = fh.read().strip()
            except OSError as exc:
                logger.warning("Could not read transcript %s: %s", path, exc)
                text = ""
            finally:
                try:
                    os.remove(path)
                except OSError:
                    pass
            return text
        time.sleep(STT_POLL_SEC)
    return None


_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".bmp", ".webp")


class FrameSource:
    """Pluggable camera source. Always yields a frame (falls back to synthetic).

    The source is chosen by CAMERA_SOURCE; see the env-var docs at the top.
    A single OpenCV capture / connection is reused across iterations.
    """

    def __init__(self, spec: str, mini: Optional[object]) -> None:
        self.mini = mini
        self.cap = None  # lazy cv2.VideoCapture for webcam sources
        self.kind, self.target = self._classify((spec or "auto").strip())
        logger.info("Camera source: %s%s", self.kind,
                    f" -> {self.target}" if self.target is not None else "")

    @staticmethod
    def _classify(spec: str) -> tuple[str, object]:
        low = spec.lower()
        if low in ("", "auto"):
            return "auto", None
        if low in ("synthetic", "test"):
            return "synthetic", None
        if low.startswith("webcam"):
            idx = 0
            if ":" in low:
                try:
                    idx = int(low.split(":", 1)[1])
                except ValueError:
                    idx = 0
            return "webcam", idx
        if spec.isdigit():
            return "webcam", int(spec)
        if low.startswith(("http://", "https://")):
            return "url", spec
        if low.startswith("dir:"):
            return "dir", spec[4:]
        if low.startswith("file:"):
            return "file", spec[5:]
        if os.path.isdir(spec):
            return "dir", spec
        return "file", spec

    def _read_reachy(self):
        if self.mini is None:
            return None
        for attr in ("get_image", "get_camera_frame", "read_camera"):
            getter = getattr(self.mini, attr, None)
            if callable(getter):
                try:
                    return getter()
                except Exception as exc:
                    logger.debug("camera getter %s failed: %s", attr, exc)
        return None

    def _read_webcam(self, idx: int):
        if cv2 is None:
            return None
        if self.cap is None:
            self.cap = cv2.VideoCapture(idx)
        if not self.cap.isOpened():
            logger.warning("Webcam index %s not available (no video device?).", idx)
            return None
        ok, frame = self.cap.read()
        return frame if ok else None

    def _read_url(self, url: str):
        if cv2 is None:
            return None
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                data = resp.read()
        except Exception as exc:
            logger.warning("URL frame fetch failed (%s): %s", url, exc)
            return None
        img = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            logger.warning("URL did not return a decodable image: %s", url)
        return img

    def _read_file(self, path: str):
        if cv2 is None:
            return None
        img = cv2.imread(path)
        if img is None:
            logger.warning("Could not read image file: %s", path)
        return img

    def _read_dir(self, path: str):
        try:
            files = [
                os.path.join(path, f)
                for f in os.listdir(path)
                if f.lower().endswith(_IMAGE_EXTS)
            ]
        except OSError as exc:
            logger.warning("dir source failed (%s): %s", path, exc)
            return None
        if not files:
            return None
        return self._read_file(max(files, key=os.path.getmtime))

    def read(self, counter: int) -> np.ndarray:
        frame = None
        if self.kind == "auto":
            frame = self._read_reachy()
        elif self.kind == "webcam":
            frame = self._read_webcam(self.target)
        elif self.kind == "url":
            frame = self._read_url(self.target)
        elif self.kind == "dir":
            frame = self._read_dir(self.target)
        elif self.kind == "file":
            frame = self._read_file(self.target)

        if frame is None:
            if self.kind not in ("auto", "synthetic"):
                logger.info("Frame source empty this iteration; using synthetic.")
            frame = _synthetic_frame(counter)
        return frame

    def close(self) -> None:
        if self.cap is not None:
            try:
                self.cap.release()
            except Exception:
                pass
            self.cap = None


def query_gemma(
    client: "ollama.Client", messages: list, schema: dict = SCHEMA
) -> Optional[dict]:
    """Send a chat request to Gemma and parse the forced-JSON response.

    `messages` is a standard Ollama chat list (each item may carry `images`).
    Returns the parsed action dict, or None on failure.
    """
    try:
        res = client.chat(
            model=GEMMA_MODEL,
            messages=messages,
            format=schema,
            options={"temperature": LLM_TEMPERATURE},
        )
    except Exception as exc:
        logger.error("Gemma request failed: %s", exc)
        return None

    content = res.get("message", {}).get("content", "")
    try:
        action = json.loads(content)
    except (json.JSONDecodeError, TypeError) as exc:
        logger.error("Malformed JSON from Gemma (%s): %r", exc, content)
        return None

    if "expression" not in action or "movement" not in action:
        logger.error("JSON missing required keys: %r", action)
        return None
    return action


def run_loop(mini: Optional[object]) -> None:
    client = ollama.Client(host=OLLAMA_HOST)
    wheels = WheelController(reachy=mini)
    camera = FrameSource(CAMERA_SOURCE, mini)
    life = GracefulExit()

    logger.info("Ollama host: %s | model: %s", OLLAMA_HOST, GEMMA_MODEL)
    logger.info("Starting control loop (Ctrl+C to stop)...")

    counter = 0
    recent: list = []  # last few "expression/movement" choices, to vary behavior
    try:
        while not life.stop:
            counter += 1
            logger.info("--- iteration %d ---", counter)

            content = PROMPT
            if recent:
                content += (
                    f" Your last reactions were: {', '.join(recent)}. "
                    "Pick a noticeably different reaction this time."
                )
            frame = _encode_jpeg(camera.read(counter))
            messages = [{"role": "user", "content": content, "images": [frame]}]
            action = query_gemma(client, messages, SCHEMA)

            if action is None:
                logger.info("Skipping actuation this iteration (no valid action).")
            else:
                logger.info("Gemma decision: %s", action)
                apply_expression(mini, action["expression"])
                wheels.execute(action["movement"])
                recent.append(f"{action['expression']}/{action['movement']}")
                recent[:] = recent[-3:]

            if MAX_ITERS and counter >= MAX_ITERS:
                logger.info("Reached MAX_ITERS=%d, exiting.", MAX_ITERS)
                break

            # Interruptible sleep so Ctrl+C is responsive.
            slept = 0.0
            while slept < LOOP_INTERVAL_SEC and not life.stop:
                time.sleep(0.1)
                slept += 0.1
    finally:
        camera.close()

    logger.info("Control loop stopped.")


def chat_loop(mini: Optional[object]) -> None:
    """Interactive REPL: type a message, the robot reacts (sim) and replies.

    Keeps a running conversation so the robot remembers context. Set
    CHAT_USE_CAMERA=1 to also feed the current camera frame each turn.
    """
    client = ollama.Client(host=OLLAMA_HOST)
    wheels = WheelController(reachy=mini)
    camera = FrameSource(CAMERA_SOURCE, mini) if CHAT_USE_CAMERA else None
    life = GracefulExit()

    logger.info("Ollama host: %s | model: %s", OLLAMA_HOST, GEMMA_MODEL)
    if STT_ENABLED:
        print("\nVoice chat: speak when the host listener prompts you.")
        print(f"Waiting for transcripts in {STT_DIR}. Ctrl+C to stop.\n")
    else:
        print("\nChat with the robot. Type your message and press Enter.")
        print("Commands: 'quit' or 'exit' to stop, Ctrl+C anytime.\n")

    history: list = [{"role": "system", "content": CHAT_SYSTEM_PROMPT}]
    counter = 0
    try:
        while not life.stop:
            if STT_ENABLED:
                text = listen_text(life)
                if text is None:  # shutting down
                    break
                text = text.strip()
                if text:
                    print(f"you> {text}")
            else:
                try:
                    text = input("you> ").strip()
                except (EOFError, KeyboardInterrupt):
                    print()
                    break
            if not text:
                continue
            if text.lower() in ("quit", "exit"):
                break

            counter += 1
            user_msg: dict = {"role": "user", "content": text}
            if camera is not None:
                user_msg["images"] = [_encode_jpeg(camera.read(counter))]
            history.append(user_msg)

            action = query_gemma(client, history, CHAT_SCHEMA)
            if action is None:
                print("robot> (couldn't decide - try rephrasing)")
                history.pop()  # drop the failed turn so history stays clean
                continue

            reply = action.get("reply", "")
            print(f"robot> {reply}  [{action['expression']} / {action['movement']}]")
            reply_sink(reply)
            speak(reply)
            apply_expression(mini, action["expression"])
            wheels.execute(action["movement"])
            history.append({"role": "assistant", "content": reply})
    finally:
        if camera is not None:
            camera.close()

    logger.info("Chat ended.")


def main() -> int:
    mini = connect_reachy()
    runner = chat_loop if INPUT_MODE == "chat" else run_loop
    try:
        if mini is not None and hasattr(mini, "__enter__"):
            with mini as connected:
                runner(connected)
        else:
            runner(mini)
    finally:
        logger.info("Bye.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
