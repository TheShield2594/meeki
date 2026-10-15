"""The pet window: frameless, transparent, always on top, draggable."""

from __future__ import annotations

import time

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QRegion
from PySide6.QtWidgets import QApplication, QWidget

from .timer import Phase, PomodoroTimer, State, format_mmss
from .wayland import running_on_wayland

WINDOW_TITLE = "Pomodoro Pet"

# frames per second for each animation
FPS = {"idle": 3, "focusing": 4, "break": 2, "celebrating": 8}

PHASE_COLORS = {
    Phase.FOCUS: QColor("#e2574c"),
    Phase.SHORT_BREAK: QColor("#43a047"),
    Phase.LONG_BREAK: QColor("#1e88e5"),
}


class PetWindow(QWidget):
    clicked = Signal()
    quit_requested = Signal()
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
        self.setMouseTracking(True)

        s = scale
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

        self._anim = QTimer(self)
        self._anim.timeout.connect(self._advance)
        self._move_debounce = QTimer(self, singleShot=True, interval=400)
        self._move_debounce.timeout.connect(lambda: self.moved.emit(self.x(), self.y()))

        timer.tick.connect(self._on_tick)
        timer.state_changed.connect(lambda *_: self._refresh_state())
        timer.phase_changed.connect(lambda *_: self._refresh_state())
        self._refresh_state()

    # ---- state & animation ---------------------------------------------
    def celebrate(self, seconds: float) -> None:
        self._celebrate_until = max(self._celebrate_until, time.monotonic() + seconds)
        self._refresh_state()
        QTimer.singleShot(int(seconds * 1000) + 50, self._refresh_state)

    def _target_state(self) -> str:
        if time.monotonic() < self._celebrate_until:
            return "celebrating"
        t = self.timer
        if t.state in (State.RUNNING, State.PAUSED):
            return "break" if t.phase.is_break else "focusing"
        return "idle"

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
            p.drawPixmap(self.sprite_rect, pm)

        self._paint_bubble(p)
        self._paint_close(p)
        p.end()

    def _paint_bubble(self, p: QPainter) -> None:
        t = self.timer
        r = QRectF(self.bubble_rect)
        if t.state is State.COMPLETE:
            accent, text = QColor("#f9a825"), "Done!"
        elif t.state is State.IDLE:
            accent, text = QColor("#9e9e9e"), format_mmss(self._remaining)
        else:
            accent, text = PHASE_COLORS[t.phase], format_mmss(self._remaining)

        # speech bubble with a small tail pointing at the dog
        path = QPainterPath()
        path.addRoundedRect(r, 12, 12)
        tail = QPainterPath()
        tail.moveTo(r.left() + 10, r.bottom() - 10)
        tail.lineTo(r.left() - 6, r.bottom() + 8)
        tail.lineTo(r.left() + 22, r.bottom() - 1)
        tail.closeSubpath()
        path = path.united(tail)
        p.setPen(QPen(accent, 2))
        p.setBrush(QColor(255, 255, 255, 235))
        p.drawPath(path)

        p.setFont(self.font_time)
        p.setPen(QColor("#303030") if t.state is not State.PAUSED else QColor("#8a8a8a"))
        text_rect = r.adjusted(0, 2, 0, -r.height() * 0.38)
        p.drawText(text_rect, Qt.AlignCenter, text)

        # round progress dots
        n = max(1, t.rounds)
        dot = max(4.0, min(7.0, (r.width() - 20) / (n * 1.8)))
        gap = dot * 0.8
        total = n * dot + (n - 1) * gap
        x = r.center().x() - total / 2
        y = r.bottom() - dot - 7
        done = t.round - 1 + (1 if t.phase.is_break else 0)
        if t.state is State.COMPLETE:
            done = n
        elif t.state is State.IDLE:
            done = 0
        for i in range(n):
            p.setPen(QPen(accent, 1.2))
            p.setBrush(accent if i < done else Qt.NoBrush)
            if i == done and t.state is State.RUNNING and t.phase is Phase.FOCUS:
                p.setBrush(QColor(accent.red(), accent.green(), accent.blue(), 90))
            p.drawEllipse(QRectF(x, y, dot, dot))
            x += dot + gap

        if t.state is State.PAUSED:  # pause glyph in the corner
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#8a8a8a"))
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
    def enterEvent(self, _event) -> None:
        self._hover = True
        self.update(self.close_rect.adjusted(-2, -2, 2, 2))

    def leaveEvent(self, _event) -> None:
        self._hover = self._hover_close = False
        self.update(self.close_rect.adjusted(-2, -2, 2, 2))

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            return
        pos = event.position().toPoint()
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
