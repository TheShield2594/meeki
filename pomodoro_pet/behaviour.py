"""The pup's own little life: petting detection and idle animations.

Both are plain logic over (position, time) and (time, randomness) so they're
testable without a window.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable

from .i18n import _


class RubDetector:
    """Spots petting: the pointer rubbing back and forth over the pup.

    A "stroke" is a run of movement in one horizontal direction that covers
    at least `min_travel` pixels. `strokes` direction changes within
    `window` seconds count as a rub. Plain hovering or passing over never does.
    """

    def __init__(self, min_travel: float = 8, strokes: int = 3, window: float = 1.6):
        self.min_travel, self.strokes, self.window = min_travel, strokes, window
        self.reset()

    def reset(self) -> None:
        self._last_x: float | None = None
        self._direction = 0
        self._travel = 0.0
        self._turns: list[float] = []

    def feed(self, x: float, t: float) -> bool:
        """Pointer at `x` (pixels) at time `t` (seconds). True when a rub is detected."""
        if self._last_x is None:
            self._last_x = x
            return False
        dx = x - self._last_x
        self._last_x = x
        if dx == 0:
            return False
        direction = 1 if dx > 0 else -1
        if direction == self._direction:
            self._travel += abs(dx)
            return False
        # direction changed: the previous stroke counts if it was long enough
        if self._direction != 0 and self._travel >= self.min_travel:
            self._turns.append(t)
        self._direction = direction
        self._travel = abs(dx)
        self._turns = [turn for turn in self._turns if t - turn <= self.window]
        if len(self._turns) >= self.strokes:
            self._turns.clear()
            return True
        return False


class IdleLife:
    """Decides what the pup does when nothing's going on.

    tick() is called every couple of seconds with whether the pup is plainly
    idle (timer not running, no other animation) and which one-shot animations
    exist. It returns an animation name to play, "nap" to lie down, or None.
    Any interaction (touch) wakes it and resets the clock.
    """

    ACTIONS = {"look": 4, "yawn": 2, "scratch": 2, "stretch": 1}  # name: weight

    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        rng: random.Random | None = None,
        nap_after: float = 300,
        gap: tuple[float, float] = (25, 70),
    ):
        self.clock = clock
        self.rng = rng or random.Random()
        self.nap_after = nap_after
        self.gap = gap
        self.napping = False
        self._last_touch = clock()
        self._next_action = self._last_touch + self.rng.uniform(*gap)
        self._last_action: str | None = None

    def touch(self) -> bool:
        """Something happened (hover, click, timer change). True if it woke the pup."""
        now = self.clock()
        self._last_touch = now
        woke = self.napping
        self.napping = False
        if self._next_action < now + 5:
            self._next_action = now + self.rng.uniform(*self.gap)
        return woke

    def tick(self, idle: bool, available) -> str | None:
        now = self.clock()
        if not idle:
            self.napping = False
            self._next_action = max(self._next_action, now + self.gap[0])
            return None
        if self.napping:
            return None
        if now - self._last_touch >= self.nap_after:
            self.napping = True
            return "nap"
        if now < self._next_action:
            return None
        self._next_action = now + self.rng.uniform(*self.gap)
        choices = {k: w for k, w in self.ACTIONS.items() if k in available and k != self._last_action}
        if not choices:
            return None
        action = self.rng.choices(list(choices), weights=list(choices.values()))[0]
        self._last_action = action
        return action


PET_LINES = [_("*happy wiggles* 🐾"), _("More scritches please! 🥰"), _("Best human ever 💕"),
             _("Right behind the ears! 🐶")]
