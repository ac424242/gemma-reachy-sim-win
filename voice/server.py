"""Browser voice bridge for Reachy Mini chat.

The container has no mic/speaker; the browser on Windows captures audio and
plays replies. This service transcribes uploads (faster-whisper), drops the
transcript into STT_DIR for the reachy-sim control loop (STT=1), polls
REPLY_DIR for the robot's answer, synthesizes it with Piper, and returns WAV
audio to the browser.

Run via docker compose (see README). Open http://localhost:7860 and push to talk.
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import os
import subprocess
import tempfile
import time
import wave
from pathlib import Path
from typing import Optional

import numpy as np
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from faster_whisper import WhisperModel
from piper import PiperVoice

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-11s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("voice")

WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "base")
WHISPER_DEVICE = os.environ.get("WHISPER_DEVICE", "cpu")
WHISPER_COMPUTE = os.environ.get("WHISPER_COMPUTE", "int8")
STT_DIR = Path(os.environ.get("STT_DIR", "/data/stt_in"))
REPLY_DIR = Path(os.environ.get("REPLY_DIR", "/data/replies"))
PIPER_VOICE = os.environ.get("PIPER_VOICE", "en_US-amy-medium")
PIPER_VOICE_DIR = Path(os.environ.get("PIPER_VOICE_DIR", "/app/voices"))
REPLY_TIMEOUT_SEC = float(os.environ.get("REPLY_TIMEOUT_SEC", "30"))
REPLY_POLL_SEC = float(os.environ.get("REPLY_POLL_SEC", "0.25"))

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Reachy Voice Bridge")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

_turn_lock = asyncio.Lock()
_whisper: Optional[WhisperModel] = None
_piper: Optional[PiperVoice] = None


def _get_whisper() -> WhisperModel:
    global _whisper
    if _whisper is None:
        logger.info(
            "Loading whisper model=%s device=%s compute=%s",
            WHISPER_MODEL, WHISPER_DEVICE, WHISPER_COMPUTE,
        )
        _whisper = WhisperModel(
            WHISPER_MODEL, device=WHISPER_DEVICE, compute_type=WHISPER_COMPUTE,
        )
    return _whisper


def _get_piper() -> PiperVoice:
    global _piper
    if _piper is None:
        model = PIPER_VOICE_DIR / f"{PIPER_VOICE}.onnx"
        if not model.is_file():
            raise FileNotFoundError(f"Piper voice not found: {model}")
        logger.info("Loading Piper voice: %s", model)
        _piper = PiperVoice.load(str(model))
    return _piper


def _list_reply_files() -> set[str]:
    try:
        return {
            f for f in os.listdir(REPLY_DIR)
            if f.endswith(".txt") and not f.endswith(".part")
        }
    except OSError:
        return set()


def _atomic_write_text(directory: Path, text: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    ts = int(time.time() * 1000)
    final = directory / f"{ts}.txt"
    tmp = directory / f"{ts}.txt.part"
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, final)
    return final


def _decode_webm_to_float32(data: bytes) -> np.ndarray:
    """Decode browser webm/opus to mono 16 kHz float32 for Whisper."""
    with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as tmp:
        tmp.write(data)
        tmp_path = tmp.name
    try:
        proc = subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error",
                "-i", tmp_path,
                "-ar", "16000", "-ac", "1", "-f", "f32le", "-",
            ],
            capture_output=True,
            check=True,
        )
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
    if not proc.stdout:
        return np.zeros(0, dtype=np.float32)
    return np.frombuffer(proc.stdout, dtype=np.float32)


def _transcribe(audio: np.ndarray) -> str:
    if audio.size == 0:
        return ""
    model = _get_whisper()
    segments, _info = model.transcribe(audio, language="en", beam_size=5)
    return " ".join(seg.text.strip() for seg in segments).strip()


def _synthesize_wav(text: str) -> bytes:
    voice = _get_piper()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav_file:
        voice.synthesize_wav(text, wav_file)
    return buf.getvalue()


def _wait_for_reply(before: set[str]) -> str:
    deadline = time.monotonic() + REPLY_TIMEOUT_SEC
    while time.monotonic() < deadline:
        now = _list_reply_files()
        new_files = sorted(now - before)
        if new_files:
            path = REPLY_DIR / new_files[0]
            try:
                reply = path.read_text(encoding="utf-8").strip()
            except OSError as exc:
                logger.warning("Could not read reply %s: %s", path, exc)
                reply = ""
            finally:
                try:
                    path.unlink()
                except OSError:
                    pass
            return reply
        time.sleep(REPLY_POLL_SEC)
    raise TimeoutError(
        f"No reply in {REPLY_TIMEOUT_SEC}s — is the chat loop running with "
        f"STT=1 and REPLY_DIR set?"
    )


@app.on_event("startup")
async def startup() -> None:
    STT_DIR.mkdir(parents=True, exist_ok=True)
    REPLY_DIR.mkdir(parents=True, exist_ok=True)
    logger.info("STT_DIR=%s REPLY_DIR=%s", STT_DIR, REPLY_DIR)
    # Warm models at startup so the first /turn isn't slow.
    try:
        _get_whisper()
        _get_piper()
        logger.info("Models loaded.")
    except Exception as exc:
        logger.error("Model warmup failed: %s", exc)


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok"})


@app.post("/turn")
async def turn(audio: UploadFile = File(...)) -> JSONResponse:
    """Transcribe mic audio, hand off to the robot, return Piper speech."""
    async with _turn_lock:
        data = await audio.read()
        if not data:
            raise HTTPException(status_code=400, detail="Empty audio upload")

        before_replies = _list_reply_files()
        try:
            pcm = await asyncio.to_thread(_decode_webm_to_float32, data)
            transcript = await asyncio.to_thread(_transcribe, pcm)
        except subprocess.CalledProcessError as exc:
            logger.error("ffmpeg decode failed: %s", exc.stderr.decode(errors="replace"))
            raise HTTPException(status_code=400, detail="Could not decode audio") from exc
        except Exception as exc:
            logger.error("Transcription failed: %s", exc)
            raise HTTPException(status_code=500, detail="Transcription failed") from exc

        if not transcript:
            raise HTTPException(status_code=400, detail="No speech recognized")

        logger.info("Transcript: %s", transcript)
        await asyncio.to_thread(_atomic_write_text, STT_DIR, transcript)

        try:
            reply = await asyncio.to_thread(_wait_for_reply, before_replies)
        except TimeoutError as exc:
            raise HTTPException(status_code=504, detail=str(exc)) from exc

        if not reply:
            reply = "(no reply)"

        logger.info("Reply: %s", reply[:120])
        try:
            wav_bytes = await asyncio.to_thread(_synthesize_wav, reply)
        except Exception as exc:
            logger.error("TTS failed: %s", exc)
            raise HTTPException(status_code=500, detail="Speech synthesis failed") from exc

        return JSONResponse({
            "transcript": transcript,
            "reply": reply,
            "audio": base64.b64encode(wav_bytes).decode("ascii"),
            "audio_mime": "audio/wav",
        })
