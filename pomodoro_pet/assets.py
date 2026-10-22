"""Locating and loading bundled assets (sprites, sounds, icon)."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QPainter, QPixmap

log = logging.getLogger(__name__)

STATES = ("idle", "focusing", "break", "celebrating")
MOOD_LEVELS = ("sad", "sulky", "content", "happy", "overjoyed")
# Optional: "sulky" idle pose for a low mood, "petted", one-shot idle
# animations, and an idle pose per mood level ("mood_happy", ...). Missing
# ones are simply not used.
OPTIONAL_STATES = ("sulky", "petted", "look", "yawn", "scratch", "stretch") + tuple(f"mood_{m}" for m in MOOD_LEVELS)


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


def _load_fps(sprite_root: Path, state: str) -> float | None:
    """Optional frame rate for a state: a file <root>/<state>/fps holding a number."""
    try:
        fps = float((sprite_root / state / "fps").read_text().strip())
    except (OSError, ValueError):
        return None
    return fps if 0 < fps <= 60 else None


class SpriteSet(dict):
    """Frames per state, plus the frame rates the sprite set asks for (``fps``)."""

    def __init__(self):
        super().__init__()
        self.fps: dict[str, float] = {}

    def add(self, root: Path, state: str, frames: list[QPixmap]) -> None:
        self[state] = frames
        fps = _load_fps(root, state)
        if fps:
            self.fps[state] = fps


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


def load_sprites(override_dir: str | None = None) -> SpriteSet:
    roots: list[Path] = []
    if override_dir:
        roots.append(Path(os.path.expanduser(override_dir)))
    bundled = asset("sprites")
    if bundled:
        roots.append(bundled)

    sprites = SpriteSet()
    idle_roots = roots
    for state in STATES:
        for i, root in enumerate(roots):
            frames = _load_state(root, state)
            if frames:
                sprites.add(root, state, frames)
                if state == "idle":
                    idle_roots = roots[: i + 1]
                break
        else:
            log.warning("No sprite frames for state %r; using placeholder", state)
            sprites[state] = _placeholder(state)
    for state in OPTIONAL_STATES:
        # a mood pose replaces the idle pose, so it has to come from the same set:
        # custom idle art never turns into the bundled pup when the mood changes
        for root in idle_roots if state.startswith("mood_") else roots:
            frames = _load_state(root, state)
            if frames:
                sprites.add(root, state, frames)
                break
    return sprites
