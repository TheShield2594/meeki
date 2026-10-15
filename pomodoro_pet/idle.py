"""Idle detection and break coaching.

IdleSource asks the desktop how long it's been since the last keyboard/mouse
input, and whether the screen is locked:

  1. org.freedesktop.ScreenSaver.GetSessionIdleTime / GetActive
     (KDE Plasma implements both, on X11 and Wayland)
  2. org.gnome.Mutter.IdleMonitor (GNOME)
  3. the XScreenSaver extension via libXss (plain X11 sessions)

ActivityTracker polls it and turns the numbers into events (you went away,
you came back, you were active for N seconds). BreakCoach decides when to
nudge you for working through a break. Both are plain logic over numbers so
they're testable without a desktop.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import logging
import time
from collections.abc import Callable

from PySide6.QtCore import QObject, QTimer, Signal

log = logging.getLogger(__name__)


class IdleSource:
    """Seconds since last input (None if unknown) and screen-lock state."""

    def __init__(self) -> None:
        self._bus = None
        self._ifaces: dict[tuple[str, str], object] = {}
        self._backend: Callable[[], float | None] | None = None
        self._x11 = None
        try:
            from PySide6.QtDBus import QDBusConnection

            bus = QDBusConnection.sessionBus()
            if bus.isConnected():
                self._bus = bus
        except ImportError:
            pass

    # ---- D-Bus helpers -------------------------------------------------
    def _call(self, service: str, path: str, iface: str, method: str):
        if self._bus is None:
            return None
        from PySide6.QtDBus import QDBusInterface, QDBusMessage

        key = (service, path)
        proxy = self._ifaces.get(key)
        if proxy is None:
            proxy = QDBusInterface(service, path, iface, self._bus)
            proxy.setTimeout(800)  # never freeze the UI on a stuck service
            self._ifaces[key] = proxy
        reply = proxy.call(method)
        if reply.type() != QDBusMessage.ReplyMessage or not reply.arguments():
            return None
        return reply.arguments()[0]

    def _freedesktop(self) -> float | None:
        for path in ("/ScreenSaver", "/org/freedesktop/ScreenSaver"):
            ms = self._call("org.freedesktop.ScreenSaver", path, "org.freedesktop.ScreenSaver", "GetSessionIdleTime")
            if isinstance(ms, int):
                return ms / 1000
        return None

    def _gnome(self) -> float | None:
        ms = self._call(
            "org.gnome.Mutter.IdleMonitor", "/org/gnome/Mutter/IdleMonitor/Core",
            "org.gnome.Mutter.IdleMonitor", "GetIdletime",
        )
        return ms / 1000 if isinstance(ms, int) else None

    def _xss(self) -> float | None:
        from PySide6.QtGui import QGuiApplication

        if QGuiApplication.platformName() != "xcb":
            return None
        if self._x11 is None:
            self._x11 = _XScreenSaver.open() or False
        return self._x11.idle_seconds() if self._x11 else None

    # ---- public --------------------------------------------------------
    def idle_seconds(self) -> float | None:
        if self._backend is not None:
            value = self._backend()
            if value is not None:
                return value
            self._backend = None
        for name, backend in (("freedesktop", self._freedesktop), ("gnome", self._gnome), ("x11", self._xss)):
            value = backend()
            if value is not None:
                log.info("Idle detection via %s", name)
                self._backend = backend
                return value
        return None

    def locked(self) -> bool:
        for path in ("/ScreenSaver", "/org/freedesktop/ScreenSaver"):
            active = self._call("org.freedesktop.ScreenSaver", path, "org.freedesktop.ScreenSaver", "GetActive")
            if isinstance(active, bool):
                return active
        return False


class _XScreenSaver:
    class _Info(ctypes.Structure):
        _fields_ = [
            ("window", ctypes.c_ulong), ("state", ctypes.c_int), ("kind", ctypes.c_int),
            ("til_or_since", ctypes.c_ulong), ("idle", ctypes.c_ulong), ("event_mask", ctypes.c_ulong),
        ]

    def __init__(self, xlib, xss, display, root):
        self.xss, self.display, self.root = xss, display, root
        self.info = xss.XScreenSaverAllocInfo()

    @classmethod
    def open(cls) -> _XScreenSaver | None:
        x11_name, xss_name = ctypes.util.find_library("X11"), ctypes.util.find_library("Xss")
        if not x11_name or not xss_name:
            return None
        try:
            xlib, xss = ctypes.CDLL(x11_name), ctypes.CDLL(xss_name)
            xlib.XOpenDisplay.restype = ctypes.c_void_p
            xlib.XOpenDisplay.argtypes = [ctypes.c_char_p]
            xlib.XDefaultRootWindow.restype = ctypes.c_ulong
            xlib.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
            xss.XScreenSaverAllocInfo.restype = ctypes.POINTER(cls._Info)
            xss.XScreenSaverQueryInfo.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(cls._Info)]
            display = xlib.XOpenDisplay(None)
            if not display:
                return None
            return cls(xlib, xss, display, xlib.XDefaultRootWindow(display))
        except (OSError, AttributeError):
            return None

    def idle_seconds(self) -> float | None:
        if not self.xss.XScreenSaverQueryInfo(self.display, self.root, self.info):
            return None
        return self.info.contents.idle / 1000


class ActivityTracker(QObject):
    """Polls an idle source and reports:
    - away(idle_seconds): no input for `away_after` seconds, or screen locked
    - back(away_seconds): input again after being away
    - activity(seconds, active): every poll, how much time passed and whether
      you used the computer during it
    """

    away = Signal(float)
    back = Signal(float)
    activity = Signal(float, bool)

    def __init__(self, source, away_after: float = 180, poll: float = 5, clock=time.monotonic, parent=None):
        super().__init__(parent)
        self.source = source
        self.away_after = away_after
        self.poll_interval = poll
        self._clock = clock
        self.is_away = False
        self._away_since = 0.0
        self._last = None
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.poll)

    def start(self) -> None:
        self._last = self._clock()
        self._timer.start(int(self.poll_interval * 1000))

    def stop(self) -> None:
        self._timer.stop()

    def poll(self) -> None:
        self.feed(self.source.idle_seconds(), self.source.locked())

    def feed(self, idle: float | None, locked: bool = False) -> None:
        now = self._clock()
        dt = 0.0 if self._last is None else max(0.0, now - self._last)
        self._last = now
        if idle is None and not locked:
            return  # no idle information on this desktop
        idle = idle or 0.0
        if not self.is_away and (locked or idle >= self.away_after):
            self.is_away = True
            self._away_since = now - idle
            self.away.emit(float(idle))
        elif self.is_away and not locked and idle < self.poll_interval + 2:
            self.is_away = False
            self.back.emit(max(0.0, now - idle - self._away_since))
        if dt > 0:
            self.activity.emit(dt, not locked and idle < dt + 1)


class BreakCoach:
    """Tracks computer use during a break and says when to nudge.

    The first `grace` seconds are free (finishing a sentence is fine). After
    that, `nudge_after` seconds of continuous use earn a nudge, then another
    every `repeat` seconds while it continues. A poll without input resets
    the run."""

    def __init__(self, grace: float = 20, nudge_after: float = 30, repeat: float = 60):
        self.grace, self.nudge_after, self.repeat = grace, nudge_after, repeat
        self.begin()

    def begin(self) -> None:
        self.elapsed = 0.0
        self.active_seconds = 0.0
        self.run = 0.0
        self.nudges = 0
        self._next = self.nudge_after

    def feed(self, dt: float, active: bool) -> int | None:
        """Returns the nudge number (1, 2, ...) when it's time to nudge."""
        self.elapsed += dt
        if active:
            self.active_seconds += dt
        if self.elapsed <= self.grace:
            return None
        if not active:
            self.run = 0.0
            self._next = self.nudge_after
            return None
        self.run += dt
        if self.run >= self._next:
            self.nudges += 1
            self._next = self.run + self.repeat
            return self.nudges
        return None


NUDGES = [
    "Psst… it's break time 🐾 Step away for a bit!",
    "Still working? Your break is ticking away ⏳",
    "Woof! Paws off the keyboard! 🐶",
]


def nudge_text(n: int) -> str:
    return NUDGES[min(n, len(NUDGES)) - 1]
