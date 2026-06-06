"""Host-side push-to-talk transcriber for the Reachy Mini voice chat.

Records the microphone (the container can't see it) until you press Enter,
transcribes the audio with faster-whisper on the GPU, and prints the recognized
text to stdout. scripts/listen.ps1 captures that text and drops it into stt_in/
for the in-container control loop to read (STT=1).

Runs on Windows, not in the container. Install the deps once:
    pip install faster-whisper sounddevice numpy

Usage (normally invoked by listen.ps1, but works standalone):
    python scripts/transcribe.py --model small
"""

from __future__ import annotations

import argparse
import sys
import threading

import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16000  # what Whisper expects


def record_until_enter() -> np.ndarray:
    """Record mono audio at SAMPLE_RATE until the user presses Enter.

    Recording starts immediately (this process is launched the moment you want
    to talk), so just speak and press Enter when you're done.
    """
    chunks: list[np.ndarray] = []
    done = threading.Event()

    def callback(indata, _frames, _time, status):
        if status:
            print(f"(audio status: {status})", file=sys.stderr)
        chunks.append(indata.copy())

    def wait_for_enter():
        try:
            input()
        except (EOFError, KeyboardInterrupt):
            pass
        done.set()

    print("Recording... speak, then press Enter to stop.", file=sys.stderr)
    threading.Thread(target=wait_for_enter, daemon=True).start()
    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                        callback=callback):
        done.wait()

    if not chunks:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(chunks, axis=0).reshape(-1)


def transcribe(audio: np.ndarray, model_name: str) -> str:
    if audio.size == 0:
        return ""
    # Imported lazily so --help works without the heavy dependency present.
    from faster_whisper import WhisperModel

    try:
        model = WhisperModel(model_name, device="cuda", compute_type="float16")
    except Exception as exc:  # GPU unavailable / no CUDA build -> fall back to CPU
        print(f"(GPU unavailable, using CPU: {exc})", file=sys.stderr)
        model = WhisperModel(model_name, device="cpu", compute_type="int8")

    segments, _info = model.transcribe(audio, language="en", beam_size=5)
    return " ".join(seg.text.strip() for seg in segments).strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="Push-to-talk mic transcriber.")
    parser.add_argument("--model", default="small",
                        help="faster-whisper model size (tiny/base/small/medium/large-v3)")
    args = parser.parse_args()

    audio = record_until_enter()
    text = transcribe(audio, args.model)
    if text:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
