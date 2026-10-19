"""The pet window: frameless, transparent, always on top, draggable."""

from __future__ import annotations

import math
import time

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QRegion
from PySide6.QtWidgets import QApplication, QWidget

from . import theme
from .behaviour import IdleLife, RubDetector
from .i18n import _
from .timer import Phase, PomodoroTimer, State, format_mmss
from .wayland import running_on_wayland

WINDOW_TITLE = "Pomodoro Pet"

# frames per second for each animation
FPS = {
    "idle": 3, "focusing": 4, "break": 2, "celebrating": 8, "sulky": 3,
    "petted": 6, "look": 3, "yawn": 4, "scratch": 6, "stretch": 3,
}
PET_SECONDS = 2.0  # how long the petted pose lasts after the last rub

OVERTIME_COLOR = QColor("#8e24aa")
PHASE_COLORS = {
    Phase.FOCUS: QColor("#e2574c"),
    Phase.SHORT_BREAK: QColor("#43a047"),
    Phase.LONG_BREAK: QColor("#1e88e5"),
}


class PetWindow(QWidget):
    clicked = Signal()
    quit_requested = Signal()
    petted = Signal()  # a petting session started
    scrolled = Signal(int)  # wheel steps over the bubble: +1 up, -1 down
    menu_requested = Signal(QPoint)  # right-click, global position
    moved = Signal(int, int)  # global position, only emitted where it is known (X11/XWayland)

    def __init__(self, timer: PomodoroTimer, sprites: dict, scale: float = 1.0):
        super().__init__(None)
        self.timer = timer
        self.sprites = sprites
        self.setWindowTitle(WINDOW_TITLE)
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint  # honoured on X11/XWayland; KWin script handles Wayland
            | Qt.Tool  # no taskbar entry on X11
            | Qt.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_MacAlwaysShowToolWindow, True)  # macOS hides tool windows of inactive apps
        self.setMouseTracking(True)

        self.set_scale(scale)

        self._frame = 0
        self._anim_state = "idle"
        self._celebrate_until = 0.0
        self._hover = False
        self._hover_close = False
        self._press_pos: QPoint | None = None
        self._press_on_close = False
        self._dragging = False
        self._drag_offset = QPoint()
        self._remaining = timer.remaining_seconds()
        self.mood_level = "content"  # see history.mood(); drives the idle pose
        self._shake_until = 0.0
        self._shake = QTimer(self, interval=33)
        self._shake.timeout.connect(self._shake_step)
        # petting + idle life
        self._wheel = 0  # accumulated angle delta (touchpads send small steps)
        self._rub = RubDetector()
        self._petting_until = 0.0
        self._action: str | None = None  # one-shot idle animation being played
        self.idle_animations = True
        self.life = IdleLife()
        self._life_timer = QTimer(self, interval=2000)
        self._life_timer.timeout.connect(self._life_tick)
        self._life_timer.start()

        self._anim = QTimer(self)
        self._anim.timeout.connect(self._advance)
        self._move_debounce = QTimer(self, singleShot=True, interval=400)
        self._move_debounce.timeout.connect(lambda: self.moved.emit(self.x(), self.y()))

        theme.watch(self.update)  # Plasma light/dark switch
        timer.tick.connect(self._on_tick)
        timer.state_changed.connect(lambda *_: (self.life.touch(), self._refresh_state()))
        timer.phase_changed.connect(lambda *_: self._refresh_state())
        self._refresh_state()

    def set_scale(self, s: float) -> None:
        """Lay the window out for a pet size multiplier (can change live)."""
        self.sprite_size = round(112 * s)
        bubble_w, bubble_h = round(82 * s), round(44 * s)
        self.close_rect = QRect(2, 2, round(18 * s), round(18 * s))
        self.sprite_rect = QRect(0, round(14 * s), self.sprite_size, self.sprite_size)
        self.bubble_rect = QRect(self.sprite_size - round(14 * s), round(4 * s), bubble_w, bubble_h)
        width = self.bubble_rect.right() + 4
        height = self.sprite_rect.bottom() + 2
        self.setFixedSize(width, height)
        self.font_time = QFont()
        self.font_time.setPixelSize(round(17 * s))
        self.font_time.setBold(True)
        self.font_time.setStyleHint(QFont.Monospace)
        # Clicks outside the visible shapes fall through to whatever is below.
        self.setMask(
            QRegion(self.sprite_rect)
            .united(QRegion(self.bubble_rect.adjusted(-8, 0, 2, 8)))
            .united(QRegion(self.close_rect.adjusted(-2, -2, 2, 2)))
        )
        self.update()

    def set_sprites(self, sprites: dict) -> None:
        self.sprites = sprites
        self._frame = 0
        self._refresh_state()

    # ---- state & animation ---------------------------------------------
    def celebrate(self, seconds: float) -> None:
        self._celebrate_until = max(self._celebrate_until, time.monotonic() + seconds)
        self._refresh_state()
        QTimer.singleShot(int(seconds * 1000) + 50, self._refresh_state)

    SHAKE_SECONDS = 1.2

    def nudge(self) -> None:
        """Wiggle to get your attention (break enforcement)."""
        self._shake_until = time.monotonic() + self.SHAKE_SECONDS
        self._shake.start()

    def _shake_step(self) -> None:
        if time.monotonic() >= self._shake_until:
            self._shake.stop()
        self.update()

    def _shake_offset(self) -> float:
        left = self._shake_until - time.monotonic()
        if left <= 0:
            return 0.0
        return math.sin(left * 42) * self.sprite_size * 0.05 * (left / self.SHAKE_SECONDS)

    def set_mood(self, level: str) -> None:
        if level != self.mood_level:
            self.mood_level = level
            self._refresh_state()

    # ---- petting & idle life -------------------------------------------
    @property
    def being_petted(self) -> bool:
        return time.monotonic() < self._petting_until

    def _pet(self) -> None:
        started = not self.being_petted
        self._petting_until = time.monotonic() + PET_SECONDS
        self.life.touch()
        if started:
            self.petted.emit()
            self._refresh_state()
        QTimer.singleShot(int(PET_SECONDS * 1000) + 50, self._refresh_state)

    def play(self, action: str) -> None:
        """Play a one-shot animation (if those frames exist), then carry on."""
        if self.sprites.get(action):
            self._action = action
            self._refresh_state()

    def set_idle_animations(self, enabled: bool) -> None:
        self.idle_animations = enabled
        if not enabled:
            self._action = None
            self.life.touch()  # wakes it from a nap too
            self._refresh_state()

    def _life_tick(self) -> None:
        base = self._base_state()
        idle = self.idle_animations and base in ("idle", "sulky") and self._action is None and not self.being_petted
        available = {name for name in IdleLife.ACTIONS if self.sprites.get(name)} if base == "idle" else set()
        decision = self.life.tick(idle, available)
        if decision == "nap":
            self._refresh_state()
        elif decision:
            self.play(decision)

    # ---- state & animation ---------------------------------------------
    def _base_state(self) -> str:
        t = self.timer
        if t.state in (State.RUNNING, State.PAUSED):
            return "break" if t.phase.is_break else "focusing"
        if self.mood_level in ("sulky", "sad") and self.sprites.get("sulky"):
            return "sulky"
        return "idle"

    def _target_state(self) -> str:
        if time.monotonic() < self._celebrate_until:
            return "celebrating"
        base = self._base_state()
        if self.being_petted:
            return "petted" if self.sprites.get("petted") else base
        if base in ("idle", "sulky"):
            if self.life.napping:
                return "break"  # curled up for a nap
            if self._action:
                return self._action
        return base

    def _refresh_state(self) -> None:
        state = self._target_state()
        if state != self._anim_state:
            self._anim_state = state
            self._frame = 0
        paused = self.timer.state is State.PAUSED and state != "celebrating"
        if paused:
            self._anim.stop()  # freeze the pose while paused
        else:
            self._anim.start(int(1000 / FPS.get(state, 3)))
        self.update()

    def _advance(self) -> None:
        frames = self.sprites.get(self._anim_state) or [None]
        if self._action and self._anim_state == self._action and self._frame + 1 >= len(frames):
            self._action = None  # one-shot finished
            self._refresh_state()
            return
        self._frame = (self._frame + 1) % len(frames)
        self.update()

    def _on_tick(self, remaining: int) -> None:
        self._remaining = remaining
        self.update(self.bubble_rect.adjusted(-10, -2, 2, 10))

    # ---- painting ------------------------------------------------------
    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform | QPainter.TextAntialiasing)

        frames = self.sprites.get(self._anim_state) or []
        if frames:
            pm = frames[self._frame % len(frames)]
            p.drawPixmap(self.sprite_rect.translated(round(self._shake_offset()), 0), pm)
        if self._anim_state == "petted":
            self._paint_hearts(p, [(0.0, 0.80), (0.33, 0.18), (0.66, 0.55)])
        elif self._anim_state == "idle" and self.mood_level in ("happy", "overjoyed"):
            self._paint_hearts(p, [(0.0, 0.80)] + ([(0.5, 0.16)] if self.mood_level == "overjoyed" else []))

        self._paint_bubble(p)
        self._paint_close(p)
        p.end()

    def _paint_hearts(self, p: QPainter, hearts: list[tuple[float, float]]) -> None:
        """Hearts floating up beside the pup's head: (phase offset, x position) each."""
        r = QRectF(self.sprite_rect)
        frames = max(1, len(self.sprites.get(self._anim_state) or [1]))
        for offset, x_frac in hearts:
            t = ((self._frame / frames) + offset) % 1.0
            size = r.width() * (0.09 + 0.03 * t)
            cx = r.left() + r.width() * x_frac + r.width() * 0.03 * (1 - t)
            cy = r.top() + r.height() * (0.32 - 0.30 * t)
            color = QColor("#ec5486")
            color.setAlphaF(max(0.0, min(1.0, 1.4 - 1.4 * t)))
            path = QPainterPath(QPointF(cx, cy + size * 0.35))
            path.cubicTo(cx - size * 1.1, cy - size * 0.35, cx - size * 0.45, cy - size * 0.95, cx, cy - size * 0.35)
            path.cubicTo(cx + size * 0.45, cy - size * 0.95, cx + size * 1.1, cy - size * 0.35, cx, cy + size * 0.35)
            p.setPen(Qt.NoPen)
            p.setBrush(color)
            p.drawPath(path)

    def _paint_bubble(self, p: QPainter) -> None:
        t = self.timer
        r = QRectF(self.bubble_rect)
        colors = theme.bubble_colors()
        if t.state is State.COMPLETE:
            accent, text = QColor("#f9a825"), _("Done!")
        elif t.state is State.IDLE:
            accent, text = colors.idle_accent, format_mmss(self._remaining)
        elif t.in_overtime:
            accent, text = OVERTIME_COLOR, "+" + format_mmss(self._remaining)
        else:
            accent, text = PHASE_COLORS[t.phase], format_mmss(self._remaining)
        # last minute: the outline pulses once a second (one-minute warning)
        final_minute = (
            t.state is State.RUNNING and not t.in_overtime and t.warn_seconds > 0
            and t.duration(t.phase) >= 2 * t.warn_seconds and 0 < self._remaining <= t.warn_seconds
        )
        pen_width = 3.5 if final_minute and self._remaining % 2 == 0 else 2.0

        # speech bubble with a small tail pointing at the dog
        path = QPainterPath()
        path.addRoundedRect(r, 12, 12)
        tail = QPainterPath()
        tail.moveTo(r.left() + 10, r.bottom() - 10)
        tail.lineTo(r.left() - 6, r.bottom() + 8)
        tail.lineTo(r.left() + 22, r.bottom() - 1)
        tail.closeSubpath()
        path = path.united(tail)
        p.setPen(QPen(accent, pen_width))
        p.setBrush(colors.fill)
        p.drawPath(path)

        p.setFont(self.font_time)
        p.setPen(colors.text if t.state is not State.PAUSED else colors.dim_text)
        text_rect = r.adjusted(0, 2, 0, -r.height() * 0.38)
        p.drawText(text_rect, Qt.AlignCenter, text)

        # round progress dots
        n = max(1, t.rounds)
        dot = max(4.0, min(7.0, (r.width() - 20) / (n * 1.8)))
        gap = dot * 0.8
        total = n * dot + (n - 1) * gap
        x = r.center().x() - total / 2
        y = r.bottom() - dot - 7
        done = t.round - 1 + (1 if t.phase.is_break or t.in_overtime else 0)
        if t.state is State.COMPLETE:
            done = n
        elif t.state is State.IDLE:
            done = 0
        for i in range(n):
            p.setPen(QPen(accent, 1.2))
            p.setBrush(accent if i < done else Qt.NoBrush)
            if i == done and t.state is State.RUNNING and t.phase is Phase.FOCUS and not t.in_overtime:
                p.setBrush(QColor(accent.red(), accent.green(), accent.blue(), 90))
            p.drawEllipse(QRectF(x, y, dot, dot))
            x += dot + gap

        if t.state is State.PAUSED:  # pause glyph in the corner
            p.setPen(Qt.NoPen)
            p.setBrush(colors.dim_text)
            px, py = r.right() - 13, r.top() + 6
            p.drawRect(QRectF(px, py, 2.5, 8))
            p.drawRect(QRectF(px + 4.5, py, 2.5, 8))

    def _paint_close(self, p: QPainter) -> None:
        alpha = 255 if self._hover_close else (200 if self._hover else 90)
        r = QRectF(self.close_rect).adjusted(1, 1, -1, -1)
        p.setPen(QPen(QColor(80, 80, 80, alpha), 1))
        p.setBrush(QColor(230, 70, 60, alpha) if self._hover_close else QColor(255, 255, 255, alpha))
        p.drawEllipse(r)
        pen = QPen(QColor(255, 255, 255, alpha) if self._hover_close else QColor(70, 70, 70, alpha), 1.8)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        c = r.center()
        d = r.width() * 0.2
        p.drawLine(QPointF(c.x() - d, c.y() - d), QPointF(c.x() + d, c.y() + d))
        p.drawLine(QPointF(c.x() - d, c.y() + d), QPointF(c.x() + d, c.y() - d))

    # ---- mouse ---------------------------------------------------------
    def wheelEvent(self, event) -> None:
        if not self.bubble_rect.adjusted(-8, -4, 4, 8).contains(event.position().toPoint()):
            event.ignore()
            return
        self._wheel += event.angleDelta().y()
        steps = int(self._wheel / 120)
        if steps:
            self._wheel -= steps * 120
            self.scrolled.emit(steps)
        event.accept()

    def contextMenuEvent(self, event) -> None:
        self.life.touch()
        self.menu_requested.emit(event.globalPos())
        event.accept()

    def changeEvent(self, event) -> None:
        if event.type() in (QEvent.PaletteChange, QEvent.ApplicationPaletteChange):
            self.update()  # colour scheme changed
        super().changeEvent(event)

    def enterEvent(self, _event) -> None:
        self._hover = True
        if self.life.touch():  # you woke it from a nap
            self._refresh_state()
            self.play("yawn")
        self.update(self.close_rect.adjusted(-2, -2, 2, 2))

    def leaveEvent(self, _event) -> None:
        self._hover = self._hover_close = False
        self._rub.reset()
        self.unsetCursor()
        self.update(self.close_rect.adjusted(-2, -2, 2, 2))

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            return
        pos = event.position().toPoint()
        self.life.touch()
        self._press_pos = pos
        self._press_on_close = self.close_rect.adjusted(-2, -2, 2, 2).contains(pos)
        self._dragging = False
        self._drag_offset = pos

    def mouseMoveEvent(self, event) -> None:
        pos = event.position().toPoint()
        over_close = self.close_rect.adjusted(-2, -2, 2, 2).contains(pos)
        if over_close != self._hover_close:
            self._hover_close = over_close
            self.update(self.close_rect.adjusted(-2, -2, 2, 2))

        if not event.buttons():
            # Hovering: rubbing back and forth over the pup is petting.
            on_pup = self.sprite_rect.contains(pos)
            self.setCursor(Qt.OpenHandCursor if on_pup else Qt.ArrowCursor)
            if on_pup and self._rub.feed(pos.x(), time.monotonic()):
                self._pet()
            elif not on_pup:
                self._rub.reset()
        if self._press_pos is None or not (event.buttons() & Qt.LeftButton):
            return
        if self._dragging:
            # manual move fallback (only reached if startSystemMove failed)
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            return
        if (pos - self._press_pos).manhattanLength() < QApplication.startDragDistance():
            return
        self._dragging = True
        handle = self.windowHandle()
        if handle is not None and handle.startSystemMove():
            # The compositor owns the drag now; we may never see the release.
            self._press_pos = None
        elif running_on_wayland():
            self._press_pos = None  # no way to move ourselves on Wayland

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.LeftButton or self._press_pos is None:
            self._press_pos = None
            return
        pos = event.position().toPoint()
        was_drag = self._dragging
        self._press_pos = None
        self._dragging = False
        if was_drag:
            return
        if self._press_on_close and self.close_rect.adjusted(-2, -2, 2, 2).contains(pos):
            self.quit_requested.emit()
        elif not self._press_on_close:
            self.clicked.emit()

    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        if not running_on_wayland():
            self._move_debounce.start()

    def interaction_rect(self) -> QRect:
        """Area the popover should be placed next to."""
        return self.sprite_rect.united(self.bubble_rect)
