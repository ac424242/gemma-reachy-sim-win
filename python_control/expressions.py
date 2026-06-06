"""Map Gemma's `expression` field to Reachy Mini head poses + antenna gestures.

Reachy Mini expresses emotion through its Stewart-platform head (translation +
orientation) and two antennas. Each named expression is a short sequence of
keyframes so motions like "happy" can wiggle rather than snap to a single pose.

The actual motion goes through the ReachyMini SDK -> daemon ->
reachy-mini-motor-controller stack. When no robot/sim is connected (`mini` is
None) the gestures are logged instead, so the control logic stays testable.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger("expressions")

# Each keyframe: head pose kwargs passed to create_head_pose (translation in mm,
# angles in degrees), antenna targets in radians, and the move duration (s).
# Antennas are [left, right]; positive lifts/perks, negative droops.
_EXPRESSIONS: dict[str, list[dict]] = {
    "happy": [
        {"head": {"z": 15, "pitch": -8}, "antennas": [0.8, 0.8], "duration": 0.3},
        {"head": {"z": 15, "roll": 8}, "antennas": [0.9, 0.6], "duration": 0.25},
        {"head": {"z": 15, "roll": -8}, "antennas": [0.6, 0.9], "duration": 0.25},
        {"head": {"z": 10}, "antennas": [0.8, 0.8], "duration": 0.3},
    ],
    "sad": [
        {"head": {"z": -12, "pitch": 18}, "antennas": [-0.7, -0.7], "duration": 0.8},
    ],
    "curious": [
        {"head": {"roll": 18, "yaw": 12}, "antennas": [0.6, -0.2], "duration": 0.5},
        {"head": {"roll": -18, "yaw": -12}, "antennas": [-0.2, 0.6], "duration": 0.5},
    ],
    "angry": [
        {"head": {"z": -6, "pitch": -20}, "antennas": [-0.9, -0.9], "duration": 0.25},
        {"head": {"z": -6, "pitch": -20, "yaw": 6}, "antennas": [-0.9, -0.9], "duration": 0.15},
        {"head": {"z": -6, "pitch": -20, "yaw": -6}, "antennas": [-0.9, -0.9], "duration": 0.15},
    ],
    "neutral": [
        {"head": {}, "antennas": [0.0, 0.0], "duration": 0.5},
    ],
}

VALID_EXPRESSIONS: list[str] = list(_EXPRESSIONS.keys())


def apply_expression(mini: Optional[object], expression: str) -> None:
    """Drive the head + antennas for the given expression name.

    `mini` is a connected `reachy_mini.ReachyMini` instance, or None for a
    dry run (gestures are logged only).
    """
    name = (expression or "").strip().lower()
    if name not in _EXPRESSIONS:
        logger.warning("Unknown expression %r, falling back to 'neutral'", expression)
        name = "neutral"

    keyframes = _EXPRESSIONS[name]
    logger.info("expression -> %s (%d keyframe(s))", name, len(keyframes))

    if mini is None:
        for i, kf in enumerate(keyframes):
            logger.info("  [dry-run] keyframe %d: head=%s antennas=%s", i, kf["head"], kf["antennas"])
        return

    # Imported lazily so the module is importable without the SDK installed.
    from reachy_mini.utils import create_head_pose

    for kf in keyframes:
        head_pose = create_head_pose(mm=True, degrees=True, **kf["head"])
        mini.goto_target(
            head=head_pose,
            antennas=kf["antennas"],
            duration=kf["duration"],
        )
