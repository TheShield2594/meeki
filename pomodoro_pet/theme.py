"""Follow the desktop's light/dark scheme and accent colour.

On Plasma, Qt picks up the colour scheme through the platform theme (with
Arch's pyside6 package, which uses the system Qt and plasma-integration).
Windows built from palette() roles follow automatically; this module covers
the things we paint ourselves: the speech bubble and the break overlay.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication, QPalette


def is_dark() -> bool:
    hints = QGuiApplication.styleHints()
    scheme = getattr(hints, "colorScheme", None)
    if scheme is not None:
        value = scheme()
        if value == Qt.ColorScheme.Dark:
            return True
        if value == Qt.ColorScheme.Light:
            return False
    window = QGuiApplication.palette().color(QPalette.Window)
    return window.lightnessF() < 0.5


def accent() -> QColor:
    """The desktop accent colour (Plasma: System Settings -> Colors -> Accent)."""
    return QGuiApplication.palette().color(QPalette.Highlight)


@dataclass
class BubbleColors:
    fill: QColor
    text: QColor
    dim_text: QColor
    idle_accent: QColor


def bubble_colors() -> BubbleColors:
    if is_dark():
        return BubbleColors(QColor(42, 44, 50, 240), QColor("#ececec"), QColor("#9a9a9a"), accent())
    return BubbleColors(QColor(255, 255, 255, 235), QColor("#303030"), QColor("#8a8a8a"), accent())


def overlay_style() -> str:
    """Stylesheet for the break overlay card."""
    if is_dark():
        card, text, tip = "rgba(36, 38, 44, 240)", "#ececec", "#b8b8b8"
    else:
        card, text, tip = "rgba(255, 255, 255, 235)", "#303030", "#555555"
    time_color = accent().name()
    return f"""
QWidget#card {{ background: {card}; border-radius: 22px; }}
QLabel {{ color: {text}; }}
QLabel#title {{ font-size: 22pt; font-weight: 700; }}
QLabel#time {{ font-size: 44pt; font-weight: 700; color: {time_color}; }}
QLabel#tip {{ font-size: 13pt; color: {tip}; }}
QPushButton {{ padding: 8px 18px; font-size: 11pt; }}
"""


def watch(callback) -> None:
    """Call `callback()` whenever the colour scheme changes (Qt 6.5+)."""
    hints = QGuiApplication.styleHints()
    signal = getattr(hints, "colorSchemeChanged", None)
    if signal is not None:
        signal.connect(lambda *_: callback())
