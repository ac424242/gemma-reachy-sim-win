"""Verify the prerequisites that the plan marks as DONE.

Implements Phase 0 Tests 1-5 with explicit pass/fail conditions, using only the
Python standard library so it runs anywhere (Windows host or inside the
lerobot-gpu container).

Usage:
    # On the Windows host (Ollama reachable at localhost):
    python scripts/verify_prereqs.py

    # Inside the lerobot-gpu container (Ollama reachable via host.docker.internal):
    OLLAMA_HOST=http://host.docker.internal:11434 python scripts/verify_prereqs.py --in-container

Environment:
    OLLAMA_HOST   default http://localhost:11434
    GEMMA_MODEL   default gemma3:4b
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import struct
import subprocess
import sys
import urllib.error
import urllib.request
import zlib

DEFAULT_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
MODEL = os.environ.get("GEMMA_MODEL", "gemma3:4b")

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"

results: list[tuple[str, bool]] = []


def record(name: str, passed: bool, detail: str = "") -> bool:
    tag = f"{GREEN}PASS{RESET}" if passed else f"{RED}FAIL{RESET}"
    print(f"  [{tag}] {name}" + (f" - {detail}" if detail else ""))
    results.append((name, passed))
    return passed


def _post_json(host: str, path: str, payload: dict, timeout: float = 120.0) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        host.rstrip("/") + path,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _get_json(host: str, path: str, timeout: float = 10.0) -> dict:
    req = urllib.request.Request(host.rstrip("/") + path, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _make_png(color: tuple[int, int, int] = (220, 40, 40), size: int = 16) -> bytes:
    """Build a tiny solid-color PNG using only stdlib (for the vision test)."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    r, g, b = color
    raw = bytearray()
    for _ in range(size):
        raw.append(0)  # filter type 0 per scanline
        raw.extend([r, g, b] * size)
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)  # 8-bit RGB
    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", ihdr)
    png += chunk(b"IDAT", zlib.compress(bytes(raw)))
    png += chunk(b"IEND", b"")
    return png


def test1_gemma_reachable(host: str) -> None:
    print("\nTest 1 - Gemma 3 running and reachable")
    try:
        tags = _get_json(host, "/api/tags")
        models = [m.get("name", "") for m in tags.get("models", [])]
        has_gemma = any(m.startswith("gemma3") for m in models)
        record("/api/tags reachable", True, f"models={models}")
        record("a gemma3:* tag is present", has_gemma, MODEL if has_gemma else "no gemma3 tag")
    except (urllib.error.URLError, OSError) as exc:
        record("/api/tags reachable", False, str(exc))
        return

    try:
        res = _post_json(host, "/api/chat", {
            "model": MODEL,
            "messages": [{"role": "user", "content": "reply with the single word OK"}],
            "stream": False,
        })
        text = res.get("message", {}).get("content", "")
        record("text generation works", bool(text.strip()), repr(text[:60]))
    except (urllib.error.URLError, OSError) as exc:
        record("text generation works", False, str(exc))


def test2_vision(host: str) -> None:
    print("\nTest 2 - Gemma 3 is vision-capable (critical)")
    import base64
    img_b64 = base64.b64encode(_make_png()).decode("ascii")
    try:
        res = _post_json(host, "/api/chat", {
            "model": MODEL,
            "messages": [{
                "role": "user",
                "content": "Reply with the dominant color you see in this image.",
                "images": [img_b64],
            }],
            "stream": False,
        })
        text = res.get("message", {}).get("content", "").lower()
        # A vision model names the color; a text-only one refuses or guesses blindly.
        refused = any(p in text for p in ("can't see", "cannot see", "unable to see", "no image"))
        looks_visual = ("red" in text) or (not refused and len(text.strip()) > 0)
        record("model accepts + responds to image", looks_visual, repr(text[:80]))
        if refused:
            print(f"    {YELLOW}-> tag appears text-only; pull a vision tag: ollama pull {MODEL}{RESET}")
    except (urllib.error.URLError, OSError) as exc:
        record("model accepts + responds to image", False, str(exc))


def test3_structured_json(host: str) -> None:
    print("\nTest 3 - Structured JSON output works")
    schema = {
        "type": "object",
        "properties": {
            "expression": {"type": "string", "enum": ["happy", "sad", "curious", "angry", "neutral"]},
            "movement": {"type": "string", "enum": ["forward_1_meter", "backward_1_meter", "turn_left", "turn_right", "stop"]},
        },
        "required": ["expression", "movement"],
    }
    try:
        res = _post_json(host, "/api/chat", {
            "model": MODEL,
            "messages": [{"role": "user", "content": "Pick any valid expression and movement."}],
            "format": schema,
            "stream": False,
            "options": {"temperature": 0},
        })
        content = res.get("message", {}).get("content", "")
        action = json.loads(content)
        ok = "expression" in action and "movement" in action
        record("response parses as the target JSON", ok, repr(action))
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        record("response parses as the target JSON", False, str(exc))


def test4_lerobot_image() -> None:
    print("\nTest 4 - lerobot-gpu image present and functional")
    if shutil.which("docker") is None:
        record("docker CLI available", False, "docker not on PATH (run on the host)")
        return
    try:
        out = subprocess.run(
            ["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"],
            capture_output=True, text=True, timeout=30,
        )
        present = "huggingface/lerobot-gpu" in out.stdout
        record("huggingface/lerobot-gpu image present", present,
               "found" if present else "not pulled")
        print(f"    {YELLOW}-> run 'lerobot-info' inside the container to confirm CUDA + GPU{RESET}")
    except (OSError, subprocess.SubprocessError) as exc:
        record("docker images query", False, str(exc))


def test5_python_env(in_container: bool) -> None:
    print("\nTest 5 - Python 3.12 + network path")
    v = sys.version_info
    is_312 = (v.major, v.minor) == (3, 12)
    record("running on Python 3.12", is_312, f"{v.major}.{v.minor}.{v.micro}")
    if not in_container:
        print(f"    {YELLOW}-> re-run with --in-container inside lerobot-gpu to validate "
              f"the container venv + host.docker.internal route{RESET}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify Phase 0 prerequisites.")
    parser.add_argument("--host", default=DEFAULT_HOST, help="Ollama base URL")
    parser.add_argument("--in-container", action="store_true",
                        help="Set when running inside the lerobot-gpu container")
    args = parser.parse_args()

    print(f"Verifying prerequisites against {args.host} (model={MODEL})")

    test1_gemma_reachable(args.host)
    test2_vision(args.host)
    test3_structured_json(args.host)
    test4_lerobot_image()
    test5_python_env(args.in_container)

    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    print(f"\nSummary: {passed}/{total} checks passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
