"""Desktop notifications and sound, without extra Python dependencies.

Notifications go through `notify-send` (libnotify), falling back to a raw
`gdbus` call to org.freedesktop.Notifications, then `kdialog`. Plasma shows all
of them as regular KDE notifications. Notifications with buttons go through
`gdbus` first (see notify_with_actions).

Sound is played with whatever PipeWire/Pulse/ALSA player exists
(`pw-play`, `paplay`, `aplay`). All commands are fire-and-forget.

On macOS notifications go through `osascript` (Notification Center) and sound
through `afplay`. Notification buttons aren't available there, so those fall
back to plain notifications.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import sys
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


def _mac_notify(summary: str, body: str) -> bool:
    # Texts go in as arguments, so nothing needs AppleScript quoting.
    return _spawn([
        "osascript",
        "-e", "on run argv",
        "-e", "display notification (item 3 of argv) with title (item 1 of argv) subtitle (item 2 of argv)",
        "-e", "end run",
        APP_NAME, summary, body,
    ])


def notify(summary: str, body: str = "", icon: str = APP_ID) -> None:
    if sys.platform == "darwin" and shutil.which("osascript"):
        if _mac_notify(summary, body):
            return
    if shutil.which("notify-send"):
        if _spawn([
            "notify-send",
            f"--app-name={APP_NAME}",
            f"--icon={icon}",
            f"--hint=string:desktop-entry:{APP_ID}",
            "--expire-time=8000",
            "--",
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
        for player in ("pw-play", "paplay", "aplay", "afplay"):
            if shutil.which(player):
                argv = [player, "-q", str(path)] if player == "aplay" else [player, str(path)]
                if _spawn(argv):
                    return
    QApplication.beep()


_pending: list = []  # keep running QProcess objects alive
_waiting: dict[int, tuple[set[str], object]] = {}  # notification id -> (button names, on_action)
_listener = None

_SERVICE = "org.freedesktop.Notifications"
_PATH = "/org/freedesktop/Notifications"


def _listen() -> bool:
    """Subscribe once to the notification server's button and close signals."""
    global _listener
    if _listener is not None:
        return True
    try:
        from PySide6.QtCore import QObject, Slot
        from PySide6.QtDBus import QDBusConnection, QDBusServiceWatcher
    except ImportError:
        return False

    class Listener(QObject):
        @Slot("uint", str)
        def action_invoked(self, nid: int, key: str) -> None:
            names, on_action = _waiting.get(nid, (set(), None))
            if key in names:
                del _waiting[nid]
                on_action(key)

        @Slot("uint", "uint")
        def closed(self, nid: int, _reason: int) -> None:
            _waiting.pop(nid, None)

        @Slot(str, str, str)
        def server_changed(self, *_owners) -> None:
            # ids are only unique per server instance: a restarted server starts
            # over, and an old id must not catch someone else's button
            _waiting.clear()

    bus = QDBusConnection.sessionBus()
    if not bus.isConnected():
        return False
    listener = Listener()
    if not (bus.connect(_SERVICE, _PATH, _SERVICE, "ActionInvoked", listener, "1action_invoked(uint,QString)")
            and bus.connect(_SERVICE, _PATH, _SERVICE, "NotificationClosed", listener, "1closed(uint,uint)")):
        return False
    listener.watcher = QDBusServiceWatcher(_SERVICE, bus, QDBusServiceWatcher.WatchForOwnerChange, listener)
    listener.watcher.serviceOwnerChanged.connect(listener.server_changed)
    _listener = listener
    return True


def notify_with_actions(summary: str, body: str, actions: list[tuple[str, str]], on_action) -> None:
    """Notification with buttons, e.g. [("resume", "Resume")]. Calls
    on_action(name) when one is clicked.

    Sent with `gdbus` and answered over QtDBus: PySide6 can't marshal the uint32
    Notify wants, and Ubuntu's AppArmor profile for notify-send (26.04+) blocks
    its GetCapabilities call, so notify-send drops the buttons there. Falls back
    to notify-send, then to a plain notification."""
    if shutil.which("gdbus") and _listen():
        _notify_via_gdbus(summary, body, actions, on_action)
    elif shutil.which("notify-send"):
        _notify_send_with_actions(summary, body, actions, on_action)
    else:
        notify(summary, body)


def _notify_via_gdbus(summary: str, body: str, actions: list[tuple[str, str]], on_action) -> None:
    from PySide6.QtCore import QProcess

    proc = QProcess()
    buttons = ", ".join(f"{_gvariant_str(name)}, {_gvariant_str(label)}" for name, label in actions)
    argv = [
        "call", "--session", "--dest", _SERVICE, "--object-path", _PATH, "--method", f"{_SERVICE}.Notify", "--",
        _gvariant_str(APP_NAME), "0", _gvariant_str(APP_ID), _gvariant_str(summary), _gvariant_str(body),
        f"[{buttons}]", f"{{'desktop-entry': <{_gvariant_str(APP_ID)}>}}", "-1",
    ]

    def finished(*_args) -> None:
        if proc not in _pending:
            return  # cancelled (app quitting)
        _pending.remove(proc)
        try:
            out = bytes(proc.readAllStandardOutput()).decode(errors="replace")
            err = bytes(proc.readAllStandardError()).decode(errors="replace").strip()
        except RuntimeError:  # the process object is already gone
            return
        match = re.search(r"uint32 (\d+)", out)
        if match:
            _waiting[int(match.group(1))] = ({name for name, _ in actions}, on_action)
            return
        log.debug("gdbus Notify failed: %s", err)
        if shutil.which("notify-send"):
            _notify_send_with_actions(summary, body, actions, on_action)
        else:
            notify(summary, body)

    proc.finished.connect(finished)
    _pending.append(proc)
    proc.start("gdbus", argv)


def _notify_send_with_actions(summary: str, body: str, actions: list[tuple[str, str]], on_action) -> None:
    """notify-send (libnotify 0.7.10+) waits for a click and prints the button's name."""
    from PySide6.QtCore import QProcess

    proc = QProcess()
    argv = [f"--app-name={APP_NAME}", f"--icon={APP_ID}", f"--hint=string:desktop-entry:{APP_ID}"]
    argv += [f"--action={name}={label}" for name, label in actions]
    argv += ["--", summary, body]

    def finished(*_args) -> None:
        if proc not in _pending:
            return  # cancelled (app quitting)
        _pending.remove(proc)
        try:
            choice = bytes(proc.readAllStandardOutput()).decode(errors="replace").strip()
        except RuntimeError:  # the process object is already gone
            return
        if proc.exitCode() != 0 and not choice:
            log.debug("notify-send --action failed: %s", bytes(proc.readAllStandardError()).decode(errors="replace"))
        if choice in {name for name, _ in actions}:
            on_action(choice)

    proc.finished.connect(finished)
    _pending.append(proc)
    proc.start("notify-send", argv)


def cancel_pending() -> None:
    """Stop waiting for notification buttons (on quit). The notifications
    themselves stay in the desktop's history."""
    _waiting.clear()
    while _pending:
        proc = _pending.pop()
        try:
            proc.kill()
            proc.waitForFinished(500)
        except RuntimeError:
            pass
