"""Tell people when a newer version is out.

About once a day the app asks GitHub for the latest release
(releases/latest skips drafts and pre-releases) and compares its tag with
__version__. That is the only network request the app makes, it runs in a
background thread, and any failure (offline, no releases yet, rate
limit) is silently ignored. Turn it off with "check_for_updates": false.

When a check last ran and which version we already announced live in
~/.local/state/pomodoro-pet/updates.json, so each new version is announced once.
"""

from __future__ import annotations

import json
import logging
import os
import re
import ssl
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal

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


def is_newer(latest: str, current: str | None = None) -> bool:
    new, cur = parse_version(latest), parse_version(__version__ if current is None else current)
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


def _ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    if not ctx.cert_store_stats()["x509_ca"] and os.path.isfile("/etc/ssl/cert.pem"):
        # python.org's macOS Python (and so the frozen app) ships no CA bundle of its own
        ctx.load_verify_locations("/etc/ssl/cert.pem")
    return ctx


def fetch_latest(url: str, timeout: float = 15) -> tuple[str, str]:
    """(tag, release page) of the latest release. Raises on any failure."""
    request = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": f"pomodoro-pet/{__version__}",
    })
    context = _ssl_context() if url.startswith("https:") else None
    with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
        data = json.loads(response.read(1_000_000).decode())
    return str(data["tag_name"]), str(data.get("html_url") or RELEASES_PAGE)


class UpdateChecker(QObject):
    """Emits update_available(version, url) once per new release. The request
    runs in a background thread, so a slow network never stalls the pet."""

    update_available = Signal(str, str)
    _done = Signal(object)  # from the worker thread: (tag, page) or the exception

    def __init__(self, url: str | None = None, state_file: Path | None = None):
        super().__init__()
        self.url = url or os.environ.get("POMODORO_PET_UPDATE_URL") or LATEST_URL
        self.state_file = state_file
        self.latest: tuple[str, str] | None = None  # (version, page) of a newer release, once known
        self.busy = False
        self._stopped = False
        self._done.connect(self._on_done, Qt.ConnectionType.QueuedConnection)
        # Hourly, so a laptop that slept through the day still checks soon after waking.
        self._timer = QTimer(self, interval=60 * 60 * 1000)
        self._timer.timeout.connect(self.check_if_due)
        self._first = QTimer(self, singleShot=True, interval=FIRST_CHECK_DELAY_MS)
        self._first.timeout.connect(self.check_if_due)

    def start(self) -> None:
        self._stopped = False
        self._first.start()
        self._timer.start()

    def stop(self) -> None:
        self._stopped = True  # a check still in flight is ignored when it lands
        self._first.stop()
        self._timer.stop()

    def check_if_due(self) -> None:
        last = load_state(self.state_file).get("last_check", 0)
        if not isinstance(last, (int, float)) or not 0 <= time.time() - last < CHECK_EVERY:
            self.check()

    def check(self) -> None:
        if self.busy:
            return  # one at a time
        self.busy = True

        def work() -> None:
            try:
                result = fetch_latest(self.url)
            except Exception as exc:  # offline, 404, rate limit, bad JSON: all just "no news"
                result = exc
            self._done.emit(result)

        threading.Thread(target=work, name="update-check", daemon=True).start()

    def _on_done(self, result) -> None:
        self.busy = False
        if self._stopped:
            return
        state = load_state(self.state_file)
        if isinstance(result, Exception):
            log.debug("Update check failed: %s", result)
            # Offline or timed out (say, just after waking up): try again next hour. Only a
            # check that reached GitHub (a 404 before the first release, odd JSON) counts.
            if isinstance(result, urllib.error.HTTPError) or not isinstance(result, OSError):
                state["last_check"] = time.time()
                save_state(state, self.state_file)
            return
        state["last_check"] = time.time()
        tag, page = result
        version = tag.lstrip("v")
        if is_newer(version):
            log.info("Pomodoro Pet %s is available (you have %s): %s", version, __version__, page)
            self.latest = (version, page)
            if state.get("notified") != version:
                state["notified"] = version
                self.update_available.emit(version, page)
        save_state(state, self.state_file)
