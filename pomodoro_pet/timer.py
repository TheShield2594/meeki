"""Pomodoro state machine.

The timer is deadline based, so it doesn't drift if ticks are late. The
default clock is CLOCK_BOOTTIME, which (unlike time.monotonic) keeps counting
while the machine is suspended: close the lid for an hour and the round has
really ended when you come back. The clock is injectable for tests.

If a phase ended long before we noticed (suspend, app closed), the timer does
not silently roll into the next phase; it stops at the start of it and reports
how long you were away (`away_seconds`).
"""

from __future__ import annotations

import enum
import math
import time
from collections.abc import Callable

from PySide6.QtCore import QObject, QTimer, Signal

# A phase that ended more than this many seconds before we noticed counts as
# "you were away" (suspend, app not running).
AWAY_THRESHOLD = 60.0
# Saved sessions older than this are dropped instead of restored.
MAX_RESTORE_AGE = 12 * 3600


def _default_clock() -> Callable[[], float]:
    boottime = getattr(time, "CLOCK_BOOTTIME", None)
    if boottime is not None:
        try:
            time.clock_gettime(boottime)
            return lambda: time.clock_gettime(boottime)
        except OSError:
            pass
    return time.monotonic


class Phase(enum.Enum):
    FOCUS = "focus"
    SHORT_BREAK = "short_break"
    LONG_BREAK = "long_break"

    @property
    def label(self) -> str:
        return {"focus": "Focus", "short_break": "Short break", "long_break": "Long break"}[self.value]

    @property
    def is_break(self) -> bool:
        return self is not Phase.FOCUS


class State(enum.Enum):
    IDLE = "idle"  # nothing started (or reset)
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETE = "complete"  # all rounds + long break done


class PomodoroTimer(QObject):
    tick = Signal(int)  # remaining whole seconds
    state_changed = Signal(object)  # State
    phase_changed = Signal(object)  # Phase
    phase_finished = Signal(object, object)  # finished Phase, next Phase (None when the session is over)
    session_complete = Signal()
    # every phase that ends, for the history log:
    # Phase, seconds actually spent in it, "completed" | "skipped" | "reset", round
    phase_ended = Signal(object, float, str, int)

    def __init__(
        self,
        focus_minutes: int = 25,
        short_break_minutes: int = 5,
        long_break_minutes: int = 15,
        rounds: int = 4,
        auto_advance: bool = True,
        clock: Callable[[], float] | None = None,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self._clock = clock or _default_clock()
        self.focus_minutes = focus_minutes
        self.short_break_minutes = short_break_minutes
        self.long_break_minutes = long_break_minutes
        self.rounds = rounds
        self.auto_advance = auto_advance

        self.state = State.IDLE
        self.phase = Phase.FOCUS
        self.round = 1  # 1-based focus round
        self._deadline: float | None = None  # set while RUNNING
        self._remaining = self.duration(Phase.FOCUS)  # seconds, authoritative while not RUNNING
        self._phase_total = float(self._remaining)  # length of the current phase when it began
        self._last_emitted: int | None = None
        self.away_seconds = 0.0  # set when the last phase ended while we weren't looking

        self._qtimer = QTimer(self)
        self._qtimer.setInterval(200)
        self._qtimer.timeout.connect(self.update)

    # ---- configuration -------------------------------------------------
    def duration(self, phase: Phase) -> int:
        minutes = {
            Phase.FOCUS: self.focus_minutes,
            Phase.SHORT_BREAK: self.short_break_minutes,
            Phase.LONG_BREAK: self.long_break_minutes,
        }[phase]
        return int(minutes) * 60

    def configure(self, focus: int, short: int, long: int, rounds: int) -> None:
        """Change durations. Running/paused phases keep their remaining time;
        an idle timer picks the new focus length up immediately."""
        self.focus_minutes, self.short_break_minutes, self.long_break_minutes = focus, short, long
        self.rounds = rounds
        if self.round > rounds and self.phase is Phase.FOCUS:
            self.round = rounds
        if self.state in (State.IDLE, State.COMPLETE):
            self._remaining = self.duration(self.phase)
            self._phase_total = float(self._remaining)
            self._emit_tick(force=True)

    # ---- queries -------------------------------------------------------
    def remaining(self) -> float:
        if self.state is State.RUNNING and self._deadline is not None:
            return max(0.0, self._deadline - self._clock())
        return float(self._remaining)

    def remaining_seconds(self) -> int:
        return int(math.ceil(self.remaining() - 1e-6))

    def elapsed(self) -> float:
        """Seconds spent in the current phase so far (pauses excluded)."""
        return max(0.0, self._phase_total - self.remaining())

    # ---- controls ------------------------------------------------------
    def start(self) -> None:
        if self.state is State.RUNNING:
            return
        if self.state is State.COMPLETE:
            self._reset_values()
        self._deadline = self._clock() + self._remaining
        self._set_state(State.RUNNING)
        self._qtimer.start()
        self._emit_tick(force=True)

    def pause(self) -> None:
        if self.state is not State.RUNNING:
            return
        self._remaining = self.remaining()
        self._deadline = None
        self._qtimer.stop()
        self._set_state(State.PAUSED)
        self._emit_tick(force=True)

    def toggle(self) -> None:
        if self.state is State.RUNNING:
            self.pause()
        else:
            self.start()

    def reset(self) -> None:
        if self.state in (State.RUNNING, State.PAUSED) and self.elapsed() > 0:
            self.phase_ended.emit(self.phase, self.elapsed(), "reset", self.round)
        self._qtimer.stop()
        self._reset_values()
        self.phase_changed.emit(self.phase)
        self._set_state(State.IDLE)
        self._emit_tick(force=True)

    def skip(self) -> None:
        """End the current phase now and move on to the next one."""
        if self.state is State.COMPLETE or (self.state is State.IDLE and self.phase is Phase.FOCUS and self.round == 1):
            return
        self._finish_phase(skipped=True)

    # ---- internals -----------------------------------------------------
    def _reset_values(self) -> None:
        self.phase = Phase.FOCUS
        self.round = 1
        self._deadline = None
        self._remaining = self.duration(Phase.FOCUS)
        self._phase_total = float(self._remaining)

    def _set_state(self, state: State) -> None:
        if state is not self.state:
            self.state = state
            self.state_changed.emit(state)

    def _emit_tick(self, force: bool = False) -> None:
        secs = self.remaining_seconds()
        if force or secs != self._last_emitted:
            self._last_emitted = secs
            self.tick.emit(secs)

    def update(self) -> None:
        """Called by the internal QTimer; public so tests can drive it."""
        if self.state is not State.RUNNING:
            return
        if self.remaining() <= 0:
            self._finish_phase()
        else:
            self._emit_tick()

    def _next_phase(self) -> Phase | None:
        if self.phase is Phase.FOCUS:
            return Phase.LONG_BREAK if self.round >= self.rounds else Phase.SHORT_BREAK
        if self.phase is Phase.SHORT_BREAK:
            return Phase.FOCUS
        return None  # long break ends the session

    def _finish_phase(self, skipped: bool = False) -> None:
        if skipped:
            self.phase_ended.emit(self.phase, self.elapsed(), "skipped", self.round)
        else:
            self.phase_ended.emit(self.phase, self._phase_total, "completed", self.round)
        overshoot = 0.0
        if self.state is State.RUNNING and self._deadline is not None:
            overshoot = self._clock() - self._deadline
        self.away_seconds = overshoot if overshoot > AWAY_THRESHOLD else 0.0
        finished = self.phase
        nxt = self._next_phase()
        self.phase_finished.emit(finished, nxt)

        if nxt is None:
            self._qtimer.stop()
            self._deadline = None
            self._remaining = 0
            self._set_state(State.COMPLETE)
            self._emit_tick(force=True)
            self.session_complete.emit()
            return

        if finished is Phase.SHORT_BREAK and nxt is Phase.FOCUS:
            self.round += 1
        self.phase = nxt
        self._remaining = self.duration(nxt)
        self._phase_total = float(self._remaining)
        self.phase_changed.emit(nxt)

        if self.auto_advance and self.state is State.RUNNING and not self.away_seconds:
            self._deadline = self._clock() + self._remaining
        else:
            self._qtimer.stop()
            self._deadline = None
            self._set_state(State.PAUSED)
        self._emit_tick(force=True)

    # ---- persistence -----------------------------------------------------
    def snapshot(self, wall: float | None = None) -> dict | None:
        """Serializable session state, or None if there's nothing worth keeping.
        Uses wall-clock time for the end, since the monotonic/boot clocks reset
        on reboot."""
        if self.state in (State.IDLE, State.COMPLETE):
            return None
        wall = time.time() if wall is None else wall
        data = {
            "state": self.state.value,
            "phase": self.phase.value,
            "round": self.round,
            "remaining": round(self.remaining(), 3),
            "saved_at": wall,
        }
        if self.state is State.RUNNING:
            data["ends_at"] = wall + self.remaining()
        return data

    def restore(self, data: dict | None, wall: float | None = None) -> bool:
        """Continue a saved session. A running phase whose end passed while the
        app was closed finishes now (phase_finished fires, away_seconds set)
        and the timer waits at the start of the next phase."""
        if not data:
            return False
        wall = time.time() if wall is None else wall
        try:
            state = State(data["state"])
            phase = Phase(data["phase"])
            rnd = max(1, min(int(data["round"]), self.rounds))
            remaining = max(0.0, float(data["remaining"]))
            saved_at = float(data["saved_at"])
            ends_at = float(data["ends_at"]) if state is State.RUNNING else None
        except (KeyError, TypeError, ValueError):
            return False
        if state not in (State.RUNNING, State.PAUSED) or wall - saved_at > MAX_RESTORE_AGE:
            return False

        self._qtimer.stop()
        self.phase, self.round = phase, rnd
        self._phase_total = float(self.duration(phase))
        self.phase_changed.emit(phase)
        if state is State.PAUSED:
            self._deadline = None
            self._remaining = min(remaining, self.duration(phase))
            self._set_state(State.PAUSED)
            self._emit_tick(force=True)
            return True

        self._deadline = self._clock() + (ends_at - wall)
        self._set_state(State.RUNNING)
        self._qtimer.start()
        self.update()
        self._emit_tick(force=True)
        return True


def format_mmss(seconds: int) -> str:
    seconds = max(0, int(seconds))
    m, s = divmod(seconds, 60)
    if m >= 100:
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"
