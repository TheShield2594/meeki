"""Desktop notifications and sound, without extra Python dependencies.

Notifications go through `notify-send` (libnotify), falling back to a raw
`gdbus` call to org.freedesktop.Notifications, then `kdialog`. Plasma shows all
of them as regular KDE notifications.

Sound is played with whatever PipeWire/Pulse/ALSA player exists
(`pw-play`, `paplay`, `aplay`). All commands are fire-and-forget.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

from PySide6.QtWidgets import QApplication

from .assets import asset
from .config import APP_ID

log = logging.getLogger(__name__)

APP_NAME = "Pomodoro Pet"


def _spawn(argv: list[str]) -> bool:
    try:
        subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return True
    except OSError as exc:
        log.debug("Failed to run %s: %s", argv[0], exc)
        return False


def _gvariant_str(s: str) -> str:
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"


def notify(summary: str, body: str = "", icon: str = APP_ID) -> None:
    if shutil.which("notify-send"):
        if _spawn([
            "notify-send",
            f"--app-name={APP_NAME}",
            f"--icon={icon}",
            f"--hint=string:desktop-entry:{APP_ID}",
            "--expire-time=8000",
            summary,
            body,
        ]):
            return
    if shutil.which("gdbus"):
        if _spawn([
            "gdbus", "call", "--session",
            "--dest", "org.freedesktop.Notifications",
            "--object-path", "/org/freedesktop/Notifications",
            "--method", "org.freedesktop.Notifications.Notify",
            _gvariant_str(APP_NAME), "0", _gvariant_str(icon),
            _gvariant_str(summary), _gvariant_str(body),
            "[]", f"{{'desktop-entry': <{_gvariant_str(APP_ID)}>}}", "8000",
        ]):
            return
    if shutil.which("kdialog"):
        if _spawn(["kdialog", "--title", APP_NAME, "--passivepopup", f"{summary}\n{body}".strip(), "8"]):
            return
    log.info("No notification tool found (install libnotify for notify-send)")


def play_sound(sound_file: str | None = None) -> None:
    path: Path | None = Path(sound_file).expanduser() if sound_file else asset("sounds", "chime.wav")
    if path is not None and path.is_file():
        for player in ("pw-play", "paplay", "aplay"):
            if shutil.which(player):
                argv = [player, "-q", str(path)] if player == "aplay" else [player, str(path)]
                if _spawn(argv):
                    return
    QApplication.beep()
