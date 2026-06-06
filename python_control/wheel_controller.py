"""Separate simulated wheel-motor controller for Gemma's `movement` field.

Reachy Mini is a desktop head robot with no wheels, so locomotion commands
(forward_1_meter, turn_left, ...) have no physical equivalent. This module is
the deliberately separate "wheel motor code" path from the plan: it keeps a
virtual odometry pose and logs/visualizes intent, so the full
perception -> decision -> actuation loop can be exercised in simulation.

If a connected ReachyMini instance is passed, turn commands optionally nudge the
head yaw so a turn is also visible on screen alongside the wheel log.
"""

from __future__ import annotations

import logging
import math
from typing import Optional

logger = logging.getLogger("wheels")

VALID_MOVEMENTS: list[str] = [
    "forward_1_meter",
    "backward_1_meter",
    "turn_left",
    "turn_right",
    "stop",
]

# Simple kinematics for the virtual base.
_LINEAR_SPEED_MPS = 0.5      # meters per second
_TURN_SPEED_DPS = 90.0       # degrees per second
_TURN_STEP_DEG = 45.0        # how far a single turn command rotates


class WheelController:
    """Tracks a virtual (x, y, heading) pose and reports each command's effect."""

    def __init__(self, reachy: Optional[object] = None) -> None:
        self._reachy = reachy
        self.x = 0.0
        self.y = 0.0
        self.heading_deg = 0.0

    def execute(self, movement: str) -> None:
        name = (movement or "").strip().lower()
        if name not in VALID_MOVEMENTS:
            logger.warning("Unknown movement %r, treating as 'stop'", movement)
            name = "stop"

        if name == "stop":
            logger.info("movement -> stop  (pose held at %s)", self._pose_str())
            return

        if name in ("forward_1_meter", "backward_1_meter"):
            distance = 1.0 if name == "forward_1_meter" else -1.0
            self._drive(distance)
        elif name in ("turn_left", "turn_right"):
            delta = _TURN_STEP_DEG if name == "turn_left" else -_TURN_STEP_DEG
            self._turn(delta)

    def _drive(self, distance_m: float) -> None:
        duration = abs(distance_m) / _LINEAR_SPEED_MPS
        rad = math.radians(self.heading_deg)
        self.x += distance_m * math.cos(rad)
        self.y += distance_m * math.sin(rad)
        logger.info(
            "movement -> drive %.2f m over %.1fs  -> %s",
            distance_m,
            duration,
            self._pose_str(),
        )

    def _turn(self, delta_deg: float) -> None:
        duration = abs(delta_deg) / _TURN_SPEED_DPS
        self.heading_deg = (self.heading_deg + delta_deg) % 360.0
        logger.info(
            "movement -> turn %.0f deg over %.1fs  -> %s",
            delta_deg,
            duration,
            self._pose_str(),
        )
        self._nudge_head_yaw(delta_deg)

    def _nudge_head_yaw(self, delta_deg: float) -> None:
        """Optionally show the turn on the head so it's visible in the sim."""
        if self._reachy is None:
            return
        try:
            from reachy_mini.utils import create_head_pose

            yaw = max(-30.0, min(30.0, delta_deg))
            self._reachy.goto_target(
                head=create_head_pose(yaw=yaw, mm=True, degrees=True),
                duration=0.3,
            )
            self._reachy.goto_target(
                head=create_head_pose(mm=True, degrees=True),
                duration=0.3,
            )
        except Exception as exc:  # never let a visual nudge break the loop
            logger.debug("head-yaw nudge skipped: %s", exc)

    def _pose_str(self) -> str:
        return f"pose(x={self.x:.2f}, y={self.y:.2f}, heading={self.heading_deg:.0f}deg)"
