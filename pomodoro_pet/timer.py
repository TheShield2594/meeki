"""Pomodoro state machine.

The timer is deadline based, so it doesn't drift if ticks are late. The
default clock is CLOCK_BOOTTIME, which (unlike time.monotonic) keeps counting
while the machine is suspended: close the lid for an hour and the round has
really ended when you come back. The clock is injectable for tests.

If a phase ended long before we noticed (suspend, app closed), the timer does
not silently roll into the next phase; it stops at the start of it and reports
how long you were away (`away_seconds`).

Flow mode / overtime: with `flow` on, a focus round that reaches zero counts as
completed but keeps running, counting *up* ("overtime") until you take your
break (take_break / skip). Without flow mode, extend_focus() turns a break that
just started back into overtime ("keep going"). Overtime is logged as its own
phase_ended(..., "overtime") so it adds focus time without adding a round.
"""

from __future__ import annotations

import enum
import math
import time
from collections.abc import Callable

from PySide6.QtCore import QObject, QTimer, Signal

from .i18n import _

# A phase that ended more than this many seconds before we noticed counts as
# "you were away" (suspend, app not running).
AWAY_THRESHOLD = 60.0
# Running overtime is resumed after a restart only if it was saved this recently
# (the app saves every minute during overtime); otherwise it comes back paused.
OVERTIME_RESUME_WINDOW = 180.0
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
        return {"focus": _("Focus"), "short_break": _("Short break"), "long_break": _("Long break")}[self.value]

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
    warning = Signal(object, int)  # Phase, seconds left: once per phase, `warn_seconds` before the end
    overtime_started = Signal()  # flow mode: focus target reached, now counting up

    def __init__(
        self,
        focus_minutes: int = 25,
        short_break_minutes: int = 5,
        long_break_minutes: int = 15,
        rounds: int = 4,
        auto_advance: bool = True,
        clock: Callable[[], float] | None = None,
        flow: bool = False,
        warn_seconds: int = 0,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self._clock = clock or _default_clock()
        self.focus_minutes = focus_minutes
        self.short_break_minutes = short_break_minutes
        self.long_break_minutes = long_break_minutes
        self.rounds = rounds
        self.auto_advance = auto_advance
        self.flow = flow
        self.warn_seconds = warn_seconds

        self.state = State.IDLE
        self.phase = Phase.FOCUS
        self.round = 1  # 1-based focus round
        self._deadline: float | None = None  # set while RUNNING
        self._remaining = self.duration(Phase.FOCUS)  # seconds, authoritative while not RUNNING
        self._phase_total = float(self._remaining)  # length of the current phase when it began
        self._last_emitted: int | None = None
        self.away_seconds = 0.0  # set when the last phase ended while we weren't looking
        self._warned = False
        # overtime (flow mode)
        self.in_overtime = False
        self.leaving_overtime = False  # True while phase_finished fires for "take break"
        self._ot_accum = 0.0  # overtime banked before the current running stretch
        self._ot_started: float | None = None  # clock() when the current running stretch began
        self._extendable = False  # the current break directly follows a completed focus round

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

    def overtime(self) -> float:
        """Seconds past the end of the focus round (flow mode)."""
        if not self.in_overtime:
            return 0.0
        running = self._clock() - self._ot_started if self._ot_started is not None else 0.0
        return self._ot_accum + max(0.0, running)

    def display_seconds(self) -> int:
        """What the bubble shows: time left, or overtime counting up."""
        return int(self.overtime()) if self.in_overtime else self.remaining_seconds()

    @property
    def can_extend(self) -> bool:
        """True during a break that directly follows a completed focus round."""
        return self._extendable and self.phase.is_break and self.state in (State.RUNNING, State.PAUSED)

    def elapsed(self) -> float:
        """Seconds spent in the current phase so far (pauses excluded)."""
        return max(0.0, self._phase_total - self.remaining())

    # ---- controls ------------------------------------------------------
    def start(self) -> None:
        if self.state is State.RUNNING:
            return
        if self.state is State.COMPLETE:
            self._reset_values()
        if self.in_overtime:
            self._ot_started = self._clock()
        self._deadline = self._clock() + self._remaining
        self._set_state(State.RUNNING)
        self._qtimer.start()
        self._emit_tick(force=True)

    def pause(self, rewind: float = 0.0) -> None:
        """Pause. `rewind` gives back seconds that shouldn't have counted
        (e.g. you'd already walked away), never beyond the phase length."""
        if self.state is not State.RUNNING:
            return
        if self.in_overtime:
            self._ot_accum = max(0.0, self.overtime() - max(0.0, rewind))
            self._ot_started = None
            self._remaining = 0.0
        else:
            self._remaining = min(self._phase_total, self.remaining() + max(0.0, rewind))
        self._deadline = None
        self._qtimer.stop()
        self._set_state(State.PAUSED)
        self._emit_tick(force=True)

    def credit(self, seconds: float) -> None:
        """While paused: count `seconds` as done after all (e.g. "I was working
        while away"). Starting again then finishes the phase if nothing's left."""
        if self.state is State.PAUSED:
            self._remaining = max(0.0, self._remaining - max(0.0, seconds))
            self._emit_tick(force=True)

    def adjust(self, seconds: float) -> None:
        """Add (or with a negative number, take) time from the current phase.
        Never goes below one second left; does nothing in overtime or when idle."""
        if self.in_overtime or self.state not in (State.RUNNING, State.PAUSED):
            return
        left = self.remaining()
        new_left = max(1.0, left + seconds)
        delta = new_left - left
        if self.state is State.RUNNING and self._deadline is not None:
            self._deadline += delta
        else:
            self._remaining = new_left
        self._phase_total = max(1.0, self._phase_total + delta)
        if new_left > self.warn_seconds:
            self._warned = False  # re-arm the one-minute warning if you added time
        self._emit_tick(force=True)

    def toggle(self) -> None:
        if self.state is State.RUNNING:
            self.pause()
        else:
            self.start()

    def reset(self) -> None:
        if self.in_overtime:
            self.phase_ended.emit(Phase.FOCUS, self.overtime(), "overtime", self.round)
            self._leave_overtime()
        elif self.state in (State.RUNNING, State.PAUSED) and self.elapsed() > 0:
            self.phase_ended.emit(self.phase, self.elapsed(), "reset", self.round)
        self._qtimer.stop()
        self._reset_values()
        self.phase_changed.emit(self.phase)
        self._set_state(State.IDLE)
        self._emit_tick(force=True)

    def skip(self) -> None:
        """End the current phase now and move on to the next one.
        In overtime this is "take my break" (nothing is skipped)."""
        if self.in_overtime:
            self.take_break()
            return
        if self.state is State.COMPLETE or (self.state is State.IDLE and self.phase is Phase.FOCUS and self.round == 1):
            return
        self._finish_phase(skipped=True)

    def take_break(self, trim: float = 0.0) -> None:
        """End overtime and start the break. `trim` drops seconds from the end
        of the overtime (e.g. you'd already walked away)."""
        if not self.in_overtime:
            return
        self.phase_ended.emit(Phase.FOCUS, max(0.0, self.overtime() - max(0.0, trim)), "overtime", self.round)
        self._leave_overtime()
        self.leaving_overtime = True
        try:
            self._advance(completed_focus=False)
        finally:
            self.leaving_overtime = False

    def extend_focus(self) -> bool:
        """"Keep going": turn the break that just started back into focus
        overtime, counted from the moment the focus round ended."""
        if not self.can_extend:
            return False
        break_elapsed = self.elapsed()
        self._qtimer.stop()
        self.phase = Phase.FOCUS
        self._phase_total = float(self.duration(Phase.FOCUS))
        self._remaining = 0.0
        self._deadline = self._clock()
        self.in_overtime = True
        self._ot_accum = break_elapsed
        self._ot_started = self._clock()
        self._extendable = False
        self._warned = True
        self.phase_changed.emit(Phase.FOCUS)
        self._set_state(State.RUNNING)
        self._qtimer.start()
        self._emit_tick(force=True)
        return True

    # ---- internals -----------------------------------------------------
    def _leave_overtime(self) -> None:
        self.in_overtime = False
        self._ot_accum = 0.0
        self._ot_started = None
        self._deadline = None

    def _enter_overtime(self) -> None:
        self.phase_ended.emit(Phase.FOCUS, self._phase_total, "completed", self.round)
        self.in_overtime = True
        self._ot_accum = 0.0
        self._ot_started = self._deadline  # counting from the moment it hit zero
        self.overtime_started.emit()
        self._emit_tick(force=True)

    def _reset_values(self) -> None:
        self.phase = Phase.FOCUS
        self.round = 1
        self._deadline = None
        self._remaining = self.duration(Phase.FOCUS)
        self._phase_total = float(self._remaining)
        self._warned = False
        self._extendable = False

    def _set_state(self, state: State) -> None:
        if state is not self.state:
            self.state = state
            self.state_changed.emit(state)

    def _emit_tick(self, force: bool = False) -> None:
        secs = self.display_seconds()
        if force or secs != self._last_emitted:
            self._last_emitted = secs
            self.tick.emit(secs)

    def update(self) -> None:
        """Called by the internal QTimer; public so tests can drive it."""
        if self.state is not State.RUNNING:
            return
        if self.in_overtime:
            self._emit_tick()
            return
        left = self.remaining()
        if left <= 0:
            overshoot = self._clock() - self._deadline if self._deadline is not None else 0.0
            if self.flow and self.phase is Phase.FOCUS and overshoot <= AWAY_THRESHOLD:
                self._enter_overtime()
            else:
                self._finish_phase()
            return
        if (
            not self._warned
            and self.warn_seconds > 0
            and self._phase_total >= 2 * self.warn_seconds
            and left <= self.warn_seconds
        ):
            self._warned = True
            self.warning.emit(self.phase, math.ceil(left))
        self._emit_tick()

    def next_phase(self) -> Phase | None:
        """The phase that follows the current one (None: the session ends)."""
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
        self._advance(completed_focus=not skipped and self.phase is Phase.FOCUS)

    def _advance(self, completed_focus: bool) -> None:
        """Move on from the current phase (already logged) to the next one."""
        overshoot = 0.0
        if self.state is State.RUNNING and self._deadline is not None:
            overshoot = self._clock() - self._deadline
        self.away_seconds = overshoot if overshoot > AWAY_THRESHOLD else 0.0
        finished = self.phase
        nxt = self.next_phase()
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
        self._warned = False
        self._extendable = completed_focus and nxt.is_break
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
        if self.in_overtime:
            data["overtime"] = round(self.overtime(), 3)
            if self.state is State.RUNNING:
                data["overtime_since"] = wall - self.overtime()
        elif self.state is State.RUNNING:
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
            overtime = float(data["overtime"]) if data.get("overtime") is not None else None
            overtime_since = float(data.get("overtime_since") or 0)
            ends_at = float(data["ends_at"]) if state is State.RUNNING and overtime is None else None
        except (KeyError, TypeError, ValueError):
            return False
        if state not in (State.RUNNING, State.PAUSED) or wall - saved_at > MAX_RESTORE_AGE:
            return False

        self._qtimer.stop()
        self.phase, self.round = phase, rnd
        self._phase_total = float(self.duration(phase))
        self._extendable = False
        self._warned = self.warn_seconds > 0 and remaining <= self.warn_seconds
        self.phase_changed.emit(phase)
        if overtime is not None and phase is Phase.FOCUS:
            # Running overtime resumes only after a quick restart; otherwise it
            # waits, paused, at what had been counted when it was last saved.
            live = state is State.RUNNING and wall - saved_at <= OVERTIME_RESUME_WINDOW
            self.in_overtime = True
            self._remaining = 0.0
            self._ot_accum = (wall - overtime_since) if live else overtime
            self._ot_started = self._clock() if live else None
            self._deadline = self._clock() if live else None
            self._set_state(State.RUNNING if live else State.PAUSED)
            if live:
                self._qtimer.start()
            self._emit_tick(force=True)
            return True
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
