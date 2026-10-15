"""Locating and loading bundled assets (sprites, sounds, icon)."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QPainter, QPixmap

log = logging.getLogger(__name__)

STATES = ("idle", "focusing", "break", "celebrating")
OPTIONAL_STATES = ("sulky",)  # idle pose for a low mood; falls back to "idle"


def assets_dir() -> Path | None:
    """First existing assets directory, in priority order."""
    candidates = []
    if os.environ.get("POMODORO_PET_ASSETS"):
        candidates.append(Path(os.environ["POMODORO_PET_ASSETS"]))
    candidates.append(Path(__file__).resolve().parent.parent / "assets")  # source checkout
    data_home = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    candidates.append(Path(data_home) / "pomodoro-pet" / "assets")
    data_dirs = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    candidates.extend(Path(d) / "pomodoro-pet" / "assets" for d in data_dirs.split(":") if d)
    for path in candidates:
        if (path / "sprites").is_dir():
            return path
    return None


def asset(*parts: str) -> Path | None:
    base = assets_dir()
    if base is None:
        return None
    path = base.joinpath(*parts)
    return path if path.exists() else None


def _load_state(sprite_root: Path, state: str) -> list[QPixmap]:
    """Frames for one state from either
    - a directory  <root>/<state>/*.png  (sorted by name), or
    - a horizontal sprite sheet <root>/<state>.png made of square frames."""
    frame_dir = sprite_root / state
    if frame_dir.is_dir():
        frames = [QPixmap(str(p)) for p in sorted(frame_dir.glob("*.png"))]
        return [f for f in frames if not f.isNull()]
    sheet_path = sprite_root / f"{state}.png"
    if sheet_path.is_file():
        sheet = QPixmap(str(sheet_path))
        if sheet.isNull() or sheet.height() == 0:
            return []
        size = sheet.height()
        count = max(1, sheet.width() // size)
        return [sheet.copy(QRect(i * size, 0, size, size)) for i in range(count)]
    return []


def _placeholder(state: str) -> list[QPixmap]:
    """Last-resort frames so the app still runs with no assets at all."""
    colors = {"idle": "#f5f0e6", "focusing": "#e57373", "break": "#81c784", "celebrating": "#ffd54f"}
    frames = []
    for i in range(2):
        pm = QPixmap(128, 128)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QColor(colors.get(state, "#ffffff")))
        p.setPen(QColor("#555555"))
        r = 44 + i * 4
        p.drawEllipse(64 - r, 64 - r, 2 * r, 2 * r)
        p.setBrush(QColor("#222222"))
        p.drawEllipse(46, 54, 10, 10)
        p.drawEllipse(72, 54, 10, 10)
        p.end()
        frames.append(pm)
    return frames


def load_sprites(override_dir: str | None = None) -> dict[str, list[QPixmap]]:
    roots: list[Path] = []
    if override_dir:
        roots.append(Path(os.path.expanduser(override_dir)))
    bundled = asset("sprites")
    if bundled:
        roots.append(bundled)

    sprites: dict[str, list[QPixmap]] = {}
    for state in STATES:
        frames: list[QPixmap] = []
        for root in roots:
            frames = _load_state(root, state)
            if frames:
                break
        if not frames:
            log.warning("No sprite frames for state %r; using placeholder", state)
            frames = _placeholder(state)
        sprites[state] = frames
    for state in OPTIONAL_STATES:
        for root in roots:
            frames = _load_state(root, state)
            if frames:
                sprites[state] = frames
                break
    return sprites
