"""Tell people when a newer version is out.

About once a day the app asks GitHub for the latest release
(releases/latest skips drafts and pre-releases) and compares its tag with
__version__. That is the only network request the app makes, it runs
asynchronously through Qt, and any failure (offline, no releases yet, rate
limit) is silently ignored. Turn it off with "check_for_updates": false.

When a check last ran and which version we already announced live in
~/.local/state/pomodoro-pet/updates.json, so each new version is announced once.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, QUrl, Signal

from . import __version__
from .config import _write_json_atomic, session_path

log = logging.getLogger(__name__)

REPO = "TheShield2594/meeki"
LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
CHECK_EVERY = 24 * 60 * 60
FIRST_CHECK_DELAY_MS = 60 * 1000  # let the pet settle in first


def parse_version(text: str) -> tuple[int, ...] | None:
    """"v0.3.0" / "0.3" -> (0, 3, 0) / (0, 3). None if it isn't a plain release number."""
    m = re.fullmatch(r"v?(\d+(?:\.\d+)*)", text.strip())
    return tuple(int(part) for part in m.group(1).split(".")) if m else None


def is_newer(latest: str, current: str = __version__) -> bool:
    new, cur = parse_version(latest), parse_version(current)
    if new is None or cur is None:
        return False
    width = max(len(new), len(cur))
    return new + (0,) * (width - len(new)) > cur + (0,) * (width - len(cur))


def state_path() -> Path:
    return session_path().with_name("updates.json")


def load_state(path: Path | None = None) -> dict:
    try:
        with open(path or state_path(), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(data: dict, path: Path | None = None) -> None:
    try:
        _write_json_atomic(path or state_path(), data)
    except OSError as exc:
        log.debug("Could not save update state: %s", exc)


class UpdateChecker(QObject):
    """Emits update_available(version, url) once per new release."""

    update_available = Signal(str, str)

    def __init__(self, url: str | None = None, state_file: Path | None = None):
        super().__init__()
        self.url = url or os.environ.get("POMODORO_PET_UPDATE_URL") or LATEST_URL
        self.state_file = state_file
        self.latest: tuple[str, str] | None = None  # (version, page) of a newer release, once known
        self._manager = None
        self._reply = None
        # Hourly, so a laptop that slept through the day still checks soon after waking.
        self._timer = QTimer(self, interval=60 * 60 * 1000)
        self._timer.timeout.connect(self.check_if_due)
        self._first = QTimer(self, singleShot=True, interval=FIRST_CHECK_DELAY_MS)
        self._first.timeout.connect(self.check_if_due)

    def start(self) -> None:
        self._first.start()
        self._timer.start()

    def stop(self) -> None:
        self._first.stop()
        self._timer.stop()
        if self._reply is not None:
            self._reply.abort()

    def check_if_due(self) -> None:
        last = load_state(self.state_file).get("last_check", 0)
        if not isinstance(last, (int, float)) or not 0 <= time.time() - last < CHECK_EVERY:
            self.check()

    def check(self) -> None:
        from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest

        if self._reply is not None:
            return  # one at a time
        if self._manager is None:
            self._manager = QNetworkAccessManager(self)
        request = QNetworkRequest(QUrl(self.url))
        request.setRawHeader(b"Accept", b"application/vnd.github+json")
        request.setRawHeader(b"User-Agent", f"pomodoro-pet/{__version__}".encode())
        request.setTransferTimeout(15_000)
        self._reply = self._manager.get(request)
        self._reply.finished.connect(self._on_finished)

    def _on_finished(self) -> None:
        from PySide6.QtNetwork import QNetworkReply

        reply, self._reply = self._reply, None
        reply.deleteLater()
        if reply.error() == QNetworkReply.NetworkError.OperationCanceledError:
            return
        state = load_state(self.state_file)
        state["last_check"] = time.time()
        if reply.error() != QNetworkReply.NetworkError.NoError:
            log.debug("Update check failed: %s", reply.errorString())
            save_state(state, self.state_file)
            return
        try:
            data = json.loads(bytes(reply.readAll()).decode())
            tag = str(data["tag_name"])
            page = str(data.get("html_url") or RELEASES_PAGE)
        except (ValueError, KeyError, TypeError) as exc:
            log.debug("Unexpected update check reply: %s", exc)
            save_state(state, self.state_file)
            return
        version = tag.lstrip("v")
        if is_newer(version):
            log.info("Pomodoro Pet %s is available (you have %s): %s", version, __version__, page)
            self.latest = (version, page)
            if state.get("notified") != version:
                state["notified"] = version
                self.update_available.emit(version, page)
        save_state(state, self.state_file)
