"""Settings persistence: ~/.config/pomodoro-pet/config.json."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

APP_ID = "pomodoro-pet"

DEFAULTS: dict[str, Any] = {
    # Timer
    "focus_minutes": 25,
    "short_break_minutes": 5,
    "long_break_minutes": 15,
    "rounds": 4,
    "auto_advance": True,
    # Feedback
    "notifications": True,
    "sound": True,
    "sound_file": None,  # None -> bundled chime
    # Pet window
    "position": None,  # {"x": int, "y": int} or None for "bottom-right of primary screen"
    "scale": 1.0,
    "sprite_dir": None,  # optional override directory with custom sprites
    # Platform
    "backend": "auto",  # "auto" | "wayland" | "xcb"
    "kwin_integration": True,  # use KWin scripting for keep-above + positioning on Wayland
    # Experimental (stub): hide the pet and show the countdown in the system tray instead
    "minimize_to_tray": False,
}

# (min, max) for integer settings
_INT_RANGES = {
    "focus_minutes": (1, 240),
    "short_break_minutes": (1, 120),
    "long_break_minutes": (1, 240),
    "rounds": (1, 12),
}
_BACKENDS = ("auto", "wayland", "xcb")


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return Path(base) / APP_ID


def config_path() -> Path:
    return config_dir() / "config.json"


def _sanitize(data: dict[str, Any]) -> dict[str, Any]:
    cfg = dict(DEFAULTS)
    for key, default in DEFAULTS.items():
        if key not in data:
            continue
        value = data[key]
        if key in _INT_RANGES:
            lo, hi = _INT_RANGES[key]
            try:
                cfg[key] = max(lo, min(hi, int(value)))
            except (TypeError, ValueError):
                log.warning("Ignoring invalid %s=%r", key, value)
        elif isinstance(default, bool):
            cfg[key] = bool(value)
        elif key == "scale":
            try:
                cfg[key] = max(0.5, min(4.0, float(value)))
            except (TypeError, ValueError):
                log.warning("Ignoring invalid scale=%r", value)
        elif key == "backend":
            cfg[key] = value if value in _BACKENDS else "auto"
        elif key == "position":
            if isinstance(value, dict) and isinstance(value.get("x"), int) and isinstance(value.get("y"), int):
                cfg[key] = {"x": value["x"], "y": value["y"]}
        elif key in ("sound_file", "sprite_dir"):
            cfg[key] = str(value) if value else None
    return cfg


class Config:
    """Small dict-like wrapper that knows how to load/save itself."""

    def __init__(self, path: Path | None = None):
        self.path = path or config_path()
        self.data: dict[str, Any] = dict(DEFAULTS)

    def load(self) -> Config:
        try:
            with open(self.path, encoding="utf-8") as fh:
                raw = json.load(fh)
            if not isinstance(raw, dict):
                raise ValueError("config root is not an object")
            self.data = _sanitize(raw)
        except FileNotFoundError:
            self.data = dict(DEFAULTS)
        except (OSError, ValueError) as exc:
            log.warning("Could not read %s (%s); using defaults", self.path, exc)
            self.data = dict(DEFAULTS)
        return self

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self.data, fh, indent=2, sort_keys=True)
                fh.write("\n")
            os.replace(tmp, self.path)
        except OSError as exc:
            log.warning("Could not save config to %s: %s", self.path, exc)

    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.data[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)
