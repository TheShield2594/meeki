"""Run your own scripts on timer events.

Every executable file in ~/.config/pomodoro-pet/hooks.d/ is started (in name
order, without waiting) for each event, with the event name as $1 and
details in POMODORO_* environment variables. A script that only cares about
some events just checks $1.

Events: phase_start, phase_end, paused, resumed, overtime_start,
session_complete, away, back.
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

from .config import config_dir

log = logging.getLogger(__name__)

EVENTS = ("phase_start", "phase_end", "paused", "resumed", "overtime_start", "session_complete", "away", "back")

README = """\
Pomodoro Pet hook scripts
=========================

Every executable file in this folder runs on each timer event:

    <script> EVENT

EVENT is one of: {events}

Environment variables:
    POMODORO_EVENT       same as $1
    POMODORO_PHASE       focus | short_break | long_break
    POMODORO_ROUND       current focus round (1-based)
    POMODORO_ROUNDS      rounds before the long break
    POMODORO_REMAINING   seconds left in the phase
    POMODORO_INTENTION   what you're focusing on (may be empty)
    POMODORO_PET_NAME    the pup's name
    POMODORO_OUTCOME     phase_end only: completed | skipped | reset | overtime
    POMODORO_SECONDS     phase_end: seconds spent; away/back: seconds away

Scripts start in the background; their output is discarded.
Make one executable with:  chmod +x my-hook

Example (pause music while focusing, needs playerctl):

    #!/bin/sh
    case "$1:$POMODORO_PHASE" in
      phase_start:focus) playerctl pause ;;
      phase_start:*_break) playerctl play ;;
    esac
""".format(events=", ".join(EVENTS))


def hooks_dir() -> Path:
    return config_dir() / "hooks.d"


def ensure_hooks_dir() -> Path:
    """Create the folder with a README (for "Open hooks folder")."""
    path = hooks_dir()
    path.mkdir(parents=True, exist_ok=True)
    readme = path / "README.txt"
    if not readme.exists():
        readme.write_text(README, encoding="utf-8")
    return path


def hook_scripts() -> list[Path]:
    path = hooks_dir()
    if not path.is_dir():
        return []
    return [
        f for f in sorted(path.iterdir())
        if f.is_file() and os.access(f, os.X_OK) and not f.name.startswith(".") and not f.name.endswith("~")
    ]


def run_hooks(event: str, details: dict[str, object]) -> int:
    """Start every hook script for `event`. Returns how many were started."""
    scripts = hook_scripts()
    if not scripts:
        return 0
    env = dict(os.environ)
    env["POMODORO_EVENT"] = event
    for key, value in details.items():
        env[f"POMODORO_{key.upper()}"] = "" if value is None else str(value)
    started = 0
    for script in scripts:
        try:
            subprocess.Popen(
                [str(script), event], env=env, cwd=str(script.parent),
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            started += 1
        except OSError as exc:
            log.warning("Hook %s failed to start: %s", script, exc)
    return started
