"""Full-screen break overlay ("dim the screen" break mode).

One translucent full-screen window per monitor with the napping pup, the
break countdown, a stretch/rest tip, and two ways out: snooze (a minute,
twice per break) or skip the break (which the pup will remember).
On Wayland the KWin script keeps these windows above everything else.
"""

from __future__ import annotations

import random

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .timer import format_mmss
from .wayland import OVERLAY_TITLE

MAX_SNOOZES = 2
SNOOZE_SECONDS = 60

TIPS = [
    "Stand up and stretch your back and shoulders.",
    "Look at something 20 feet away for 20 seconds.",
    "Get a glass of water.",
    "Roll your shoulders and unclench your jaw.",
    "Take five slow, deep breaths.",
    "Walk around for a minute. {name} approves.",
    "Rest your eyes. Close them for a bit.",
]

STYLE = """
QWidget#card { background: rgba(255, 255, 255, 235); border-radius: 22px; }
QLabel { color: #303030; }
QLabel#title { font-size: 22pt; font-weight: 700; }
QLabel#time { font-size: 44pt; font-weight: 700; color: #1e88e5; }
QLabel#tip { font-size: 13pt; color: #555555; }
QPushButton { padding: 8px 18px; font-size: 11pt; }
"""


class _SpriteView(QWidget):
    def __init__(self, frames, size: int):
        super().__init__()
        self.frames = frames or []
        self.frame = 0
        self.setFixedSize(size, size)

    def paintEvent(self, _event) -> None:
        if not self.frames:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.drawPixmap(self.rect(), self.frames[self.frame % len(self.frames)])
        p.end()


class _OverlayWindow(QWidget):
    snooze = Signal()
    skip = Signal()

    def __init__(self, screen, frames, pet_name: str):
        super().__init__(
            None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.NoDropShadowWindowHint
        )
        self.setWindowTitle(OVERLAY_TITLE)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setStyleSheet(STYLE)
        self._screen = screen
        self.setScreen(screen)
        self.setGeometry(screen.geometry())

        card = QWidget(self)
        card.setObjectName("card")
        card.setAttribute(Qt.WA_StyledBackground, True)
        self.sprite = _SpriteView(frames, 200)
        title = QLabel(f"Break time with {pet_name}")
        title.setObjectName("title")
        self.time = QLabel()
        self.time.setObjectName("time")
        self.tip = QLabel()
        self.tip.setObjectName("tip")
        self.tip.setWordWrap(True)
        self.snooze_btn = QPushButton("Snooze 1 min")
        self.skip_btn = QPushButton("Skip break")
        self.snooze_btn.clicked.connect(self.snooze)
        self.skip_btn.clicked.connect(self.skip)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.snooze_btn)
        buttons.addWidget(self.skip_btn)
        buttons.addStretch(1)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(40, 28, 40, 28)
        lay.setSpacing(10)
        lay.addWidget(self.sprite, 0, Qt.AlignHCenter)
        for label in (title, self.time, self.tip):  # full card width, centred text
            label.setAlignment(Qt.AlignCenter)
            label.setWordWrap(True)
            lay.addWidget(label)
        lay.addSpacing(8)
        lay.addLayout(buttons)

        outer = QVBoxLayout(self)
        outer.addStretch(1)
        outer.addWidget(card, 0, Qt.AlignHCenter)
        outer.addStretch(1)
        card.setFixedWidth(min(560, max(420, screen.geometry().width() // 3)))

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(16, 22, 34, 200))
        p.end()

    def show_on_screen(self) -> None:
        self.setScreen(self._screen)
        self.setGeometry(self._screen.geometry())
        self.showFullScreen()


class BreakOverlay(QObject):
    skip_requested = Signal()
    snoozed = Signal(int)  # snoozes used so far

    def __init__(self, frames, pet_name: str, parent: QObject | None = None):
        super().__init__(parent)
        self.frames = frames
        self.pet_name = pet_name
        self.windows: list[_OverlayWindow] = []
        self.snoozes = 0
        self.remaining = 0
        self._tip = random.choice(TIPS)
        self._anim = QTimer(self, interval=500)
        self._anim.timeout.connect(self._animate)
        self._tip_timer = QTimer(self, interval=25_000)
        self._tip_timer.timeout.connect(self._next_tip)
        self._snooze_timer = QTimer(self, singleShot=True)
        self._snooze_timer.timeout.connect(self._show)

    @property
    def active(self) -> bool:
        return bool(self.windows) or self._snooze_timer.isActive()

    def begin(self, remaining: int) -> None:
        """Start of a break: show the overlay on every screen."""
        self.snoozes = 0
        self.remaining = remaining
        self._tip = random.choice(TIPS)
        self._show()

    def end(self) -> None:
        self._snooze_timer.stop()
        self._close()

    def set_remaining(self, seconds: int) -> None:
        self.remaining = seconds
        for w in self.windows:
            w.time.setText(format_mmss(seconds))

    def _show(self) -> None:
        self._close()
        for screen in QGuiApplication.screens():
            w = _OverlayWindow(screen, self.frames, self.pet_name)
            w.snooze.connect(self._snooze)
            w.skip.connect(self.skip_requested)
            self.windows.append(w)
        self._refresh()
        for w in self.windows:
            w.show_on_screen()
        self._anim.start()
        self._tip_timer.start()

    def _close(self) -> None:
        self._anim.stop()
        self._tip_timer.stop()
        for w in self.windows:
            w.close()
            w.deleteLater()
        self.windows = []

    def _snooze(self) -> None:
        if self.snoozes >= MAX_SNOOZES:
            return
        self.snoozes += 1
        self._close()
        self.snoozed.emit(self.snoozes)
        if self.remaining > SNOOZE_SECONDS + 5:
            self._snooze_timer.start(SNOOZE_SECONDS * 1000)

    def _refresh(self) -> None:
        left = MAX_SNOOZES - self.snoozes
        for w in self.windows:
            w.time.setText(format_mmss(self.remaining))
            w.tip.setText(self._tip.format(name=self.pet_name))
            w.snooze_btn.setEnabled(left > 0)
            w.snooze_btn.setText(f"Snooze 1 min ({left} left)" if left else "No snoozes left")

    def _animate(self) -> None:
        for w in self.windows:
            w.sprite.frame += 1
            w.sprite.update()

    def _next_tip(self) -> None:
        choices = [t for t in TIPS if t != self._tip]
        self._tip = random.choice(choices)
        self._refresh()
