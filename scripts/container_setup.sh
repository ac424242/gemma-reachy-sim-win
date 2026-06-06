#!/usr/bin/env bash
# One-time setup INSIDE the lerobot-gpu container to run the Reachy Mini sim.
#
# Run as root (the image's default user can't apt-get):
#   docker exec -u root reachy-sim bash /workspace/scripts/container_setup.sh
# or from an interactive root shell:
#   bash /workspace/scripts/container_setup.sh
#
# This captures the exact recipe validated end-to-end:
#   - GL/X client libs for MuJoCo's GLFW viewer (forwarded to VcXsrv)
#   - build deps for pygobject/pycairo (reachy-mini pins pygobject<=3.46.0)
#   - GStreamer runtime + GObject typelibs (the daemon's MuJoCo backend imports Gst)
#   - the girepository-1.0.pc alias (Ubuntu 24.04 only ships gobject-introspection-1.0.pc)
#   - reachy-mini[mujoco] + the control-loop deps into the venv via uv
set -euo pipefail

VENV_PY=/lerobot/.venv/bin/python

echo "[1/5] apt: GL/X + build + gstreamer deps"
apt-get update -qq
apt-get install -y -qq \
  libgl1 libglx-mesa0 libglu1-mesa libegl1 libgl1-mesa-dri \
  libxrandr2 libxinerama1 libxcursor1 libxi6 libxext6 libxrender1 libsm6 \
  libxfixes3 libxdamage1 \
  libcairo2-dev libgirepository1.0-dev gobject-introspection libglib2.0-dev pkg-config \
  gstreamer1.0-tools gstreamer1.0-plugins-base gstreamer1.0-plugins-good \
  libgstreamer1.0-0 libgstreamer-plugins-base1.0-0 \
  gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0 \
  espeak-ng

echo "[2/5] alias girepository-1.0.pc (Ubuntu 24.04 ships gobject-introspection-1.0.pc)"
PCDIR=/usr/lib/x86_64-linux-gnu/pkgconfig
if [ ! -f "$PCDIR/girepository-1.0.pc" ]; then
  cp "$PCDIR/gobject-introspection-1.0.pc" "$PCDIR/girepository-1.0.pc"
fi
pkg-config --exists girepository-1.0 && echo "  girepository-1.0 OK"

echo "[3/5] uv pip install reachy-mini[mujoco] + loop deps"
uv pip install --python "$VENV_PY" 'reachy-mini[mujoco]' ollama opencv-python-headless numpy

echo "[4/5] verify imports"
"$VENV_PY" - <<'PY'
import reachy_mini, mujoco, ollama, cv2, numpy  # noqa: F401
print("imports OK - reachy_mini", reachy_mini.__version__)
PY

echo "[5/5] done. Next:"
echo "  Terminal 1 (sim):   DISPLAY=host.docker.internal:0.0 reachy-mini-daemon --sim"
echo "  Terminal 2 (loop):  cd /workspace/python_control && \\"
echo "                      OLLAMA_HOST=http://host.docker.internal:11434 python control_script.py"
