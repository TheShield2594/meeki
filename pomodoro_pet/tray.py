"""EXPERIMENTAL STUB: "minimize to tray" mode.

Disabled by default (config key "minimize_to_tray"). When enabled, the popover
gets a "Minimize to tray" button that hides the pet and shows the live
countdown in a system tray icon (KDE's StatusNotifierItem via
QSystemTrayIcon). Clicking the tray icon brings the pet back.

Not done yet / TODO:
  * a proper context menu (start/pause/reset from the tray)
  * remembering tray mode across restarts
  * nicer icon rendering (phase-coloured progress ring)
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QRect, Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QSystemTrayIcon

from .timer import PomodoroTimer, State, format_mmss


class TrayController(QObject):
    restore_requested = Signal()

    def __init__(self, timer: PomodoroTimer, parent: QObject | None = None):
        super().__init__(parent)
        self.timer = timer
        self.icon = QSystemTrayIcon(self)
        self.icon.activated.connect(self._on_activated)
        timer.tick.connect(self._update)

    @staticmethod
    def available() -> bool:
        return QSystemTrayIcon.isSystemTrayAvailable()

    def show(self) -> None:
        self._update(self.timer.remaining_seconds(), force=True)
        self.icon.show()

    def hide(self) -> None:
        self.icon.hide()

    def _update(self, remaining: int, force: bool = False) -> None:
        if not force and not self.icon.isVisible():
            return
        text = format_mmss(remaining)
        status = "Done!" if self.timer.state is State.COMPLETE else f"{self.timer.phase.label}: {text}"
        self.icon.setToolTip(f"Pomodoro Pet — {status}")
        self.icon.setIcon(self._render(text.split(":")[0]))

    @staticmethod
    def _render(minutes: str) -> QIcon:
        pm = QPixmap(64, 64)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QColor("#e2574c"))
        p.setPen(Qt.NoPen)
        p.drawEllipse(2, 2, 60, 60)
        font = QFont()
        font.setPixelSize(30)
        font.setBold(True)
        p.setFont(font)
        p.setPen(QColor("white"))
        p.drawText(QRect(0, 0, 64, 64), Qt.AlignCenter, minutes)
        p.end()
        return QIcon(pm)

    def _on_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.hide()
            self.restore_requested.emit()
