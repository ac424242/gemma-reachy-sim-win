FROM python:3.11-slim

# Norton (or other) TLS-intercept root CA — harmless if unused; required when
# antivirus MITM breaks pip/model downloads (same issue as reachy-sim setup).
COPY norton_root.crt /usr/local/share/ca-certificates/norton_root.crt
RUN update-ca-certificates

ENV PIP_CERT=/etc/ssl/certs/ca-certificates.crt \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt

RUN apt-get update -qq \
    && apt-get install -y -qq --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY voice/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download whisper weights + Piper voice so first /turn is fast.
RUN python - <<'PY'
from pathlib import Path
import urllib.request

from faster_whisper import WhisperModel

WhisperModel("base", device="cpu", compute_type="int8")

dest = Path("/app/voices")
dest.mkdir(parents=True, exist_ok=True)
base = (
    "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/amy/medium"
)
for name in ("en_US-amy-medium.onnx", "en_US-amy-medium.onnx.json"):
    urllib.request.urlretrieve(f"{base}/{name}", dest / name)
print("voice assets OK")
PY

COPY voice/server.py .
COPY voice/static/ static/

EXPOSE 7860
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "7860"]
