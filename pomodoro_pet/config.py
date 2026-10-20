"""Settings persistence: ~/.config/pomodoro-pet/config.json."""

from __future__ import annotations

import json
import logging
import os
import stat
import sys
import tempfile
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
    "flow_mode": False,  # when focus ends, keep counting up (overtime) until you take your break
    "minute_warning": True,  # soft heads-up one minute before a phase ends
    # Feedback
    "notifications": True,
    "dnd_during_focus": True,  # Plasma "Do Not Disturb" while a focus round runs
    # Idle detection: pause focus when you step away
    "idle_pause": True,
    "idle_minutes": 3,  # no input for this long counts as away (screen lock: immediately)
    "idle_auto_resume": False,  # resume by itself when you're back, instead of asking
    # Breaks: "off" | "nudge" (pup nags if you keep working) | "overlay" (dim the screen)
    "break_enforcement": "nudge",
    "sound": True,
    "sound_file": None,  # None -> bundled chime
    # Pet window
    "pet_name": "Biscuit",
    "hide_in_fullscreen": True,  # step aside for fullscreen videos, games and presentations
    "idle_animations": True,  # look around, yawn, scratch, stretch, nap when nothing's going on
    "position": None,  # {"x": int, "y": int} or None for "bottom-right of primary screen"
    "scale": 1.0,
    "sprite_dir": None,  # optional override directory with custom sprites
    # Platform
    "backend": "auto",  # "auto" | "wayland" | "xcb"
    "kwin_integration": True,  # use KWin scripting for keep-above + positioning on Wayland
    # Your own scripts in ~/.config/pomodoro-pet/hooks.d/ run on timer events
    "hooks_enabled": True,
    # Once a day, ask GitHub whether a newer release is out (and say so once per version)
    "check_for_updates": True,
    # Experimental (stub): hide the pet and show the countdown in the system tray instead
    "minimize_to_tray": False,
}

# (min, max) for integer settings
_INT_RANGES = {
    "focus_minutes": (1, 240),
    "short_break_minutes": (1, 120),
    "long_break_minutes": (1, 240),
    "rounds": (1, 12),
    "idle_minutes": (1, 60),
}
_BACKENDS = ("auto", "wayland", "xcb")
BREAK_MODES = ("off", "nudge", "overlay")


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return Path(base) / APP_ID


def runtime_dir() -> str:
    """Where the single-instance lock and the remote-control socket live:
    $XDG_RUNTIME_DIR, else macOS's per-user temp dir, else a private
    /tmp/pomodoro-pet-<uid> (never the shared /tmp itself)."""
    if os.environ.get("XDG_RUNTIME_DIR"):
        return os.environ["XDG_RUNTIME_DIR"]
    base = tempfile.gettempdir()
    if sys.platform == "darwin" or not hasattr(os, "getuid"):
        return base  # already per-user, and short enough for socket paths
    path = os.path.join(base, f"{APP_ID}-{os.getuid()}")
    try:
        os.mkdir(path, 0o700)
    except FileExistsError:
        pass
    except OSError as exc:
        log.warning("Could not create %s: %s", path, exc)
        return base
    st = os.lstat(path)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
        log.warning("%s is not a private directory of ours; using %s", path, base)
        return base
    return path


def config_path() -> Path:
    return config_dir() / "config.json"


def session_path() -> Path:
    """The running session (phase, round, end time), so a restart can resume it."""
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
    return Path(base) / APP_ID / "session.json"


def _write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def load_session(path: Path | None = None) -> dict | None:
    path = path or session_path()
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        log.warning("Ignoring unreadable session file %s: %s", path, exc)
        return None


def save_session(data: dict | None, path: Path | None = None) -> None:
    """Write the session, or delete the file when there's nothing to resume."""
    path = path or session_path()
    try:
        if data is None:
            path.unlink(missing_ok=True)
        else:
            _write_json_atomic(path, data)
    except OSError as exc:
        log.warning("Could not save session to %s: %s", path, exc)


def sanitize(data: dict[str, Any]) -> dict[str, Any]:
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
        elif key == "break_enforcement":
            cfg[key] = value if value in BREAK_MODES else default
        elif key == "pet_name":
            cfg[key] = str(value).strip()[:40] or default
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
            self.data = sanitize(raw)
        except FileNotFoundError:
            self.data = dict(DEFAULTS)
        except (OSError, ValueError) as exc:
            log.warning("Could not read %s (%s); using defaults", self.path, exc)
            self.data = dict(DEFAULTS)
        return self

    def save(self) -> None:
        try:
            _write_json_atomic(self.path, self.data)
        except OSError as exc:
            log.warning("Could not save config to %s: %s", self.path, exc)

    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.data[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)
