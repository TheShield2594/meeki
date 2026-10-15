"""Wayland / X11 handling.

Plain Wayland gives clients no way to (a) read or set their global window
position or (b) ask to stay above other windows. Qt silently ignores
QWidget.move() and Qt.WindowStaysOnTopHint there. We deal with that like this:

* On KDE Plasma Wayland (default): run natively and ask KWin, through its
  D-Bus scripting interface, to keep the pet above other windows, hide it from
  the taskbar/switcher, put it at the saved position, and report its position
  back to us whenever it moves (so it can be remembered). Dragging uses
  QWindow.startSystemMove(), which Wayland compositors support.
* On a non-KDE Wayland session (no KWin scripting) with backend "auto": fall
  back to XWayland (QT_QPA_PLATFORM=xcb), where positioning and keep-above work.
* backend "xcb" in the config always forces XWayland; "wayland" always forces
  native Wayland (pair it with the KWin window rule in packaging/kwin/ if the
  scripting integration is disabled).
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path

from PySide6.QtCore import QObject
from PySide6.QtGui import QGuiApplication

from .remote import DBUS_IFACE, DBUS_PATH, DBUS_SERVICE

OVERLAY_TITLE = "Pomodoro Pet — Break"  # the break overlay windows

log = logging.getLogger(__name__)

KWIN_PLUGIN = "pomodoro-pet-integration"


def is_wayland_session() -> bool:
    return os.environ.get("XDG_SESSION_TYPE") == "wayland" or bool(os.environ.get("WAYLAND_DISPLAY"))


def is_kde_session() -> bool:
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "") + ":" + os.environ.get("XDG_SESSION_DESKTOP", "")
    return "KDE" in desktop.upper() or os.environ.get("KDE_FULL_SESSION") == "true"


def choose_platform(backend: str) -> None:
    """Set QT_QPA_PLATFORM before QApplication exists. An explicit
    QT_QPA_PLATFORM from the environment always wins."""
    if os.environ.get("QT_QPA_PLATFORM"):
        log.info("Using QT_QPA_PLATFORM=%s from environment", os.environ["QT_QPA_PLATFORM"])
        return
    if not is_wayland_session():
        return  # X11 session, nothing to decide
    if backend == "xcb":
        os.environ["QT_QPA_PLATFORM"] = "xcb"
    elif backend == "wayland":
        os.environ["QT_QPA_PLATFORM"] = "wayland"
    elif not is_kde_session():
        log.info("Wayland session without KWin: falling back to XWayland for positioning/keep-above")
        os.environ["QT_QPA_PLATFORM"] = "xcb"
    else:
        os.environ["QT_QPA_PLATFORM"] = "wayland"


def running_on_wayland() -> bool:
    return QGuiApplication.platformName().startswith("wayland")


# --------------------------------------------------------------------------
# KWin scripting integration (native Wayland on Plasma)
# --------------------------------------------------------------------------

_KWIN_SCRIPT = r"""
(function () {
    var CFG = __CONFIG__;
    var handled = [];
    function windows() {
        return workspace.windowList ? workspace.windowList() : workspace.clientList();
    }
    function ours(w, caption) {
        if (!w || w.popupWindow) return false;
        var cls = String(w.resourceClass || ""), name = String(w.resourceName || "");
        return (cls === CFG.appId || name === CFG.appId) && String(w.caption) === caption;
    }
    function report(w) {
        var g = w.frameGeometry;
        callDBus(CFG.service, CFG.path, CFG.iface, "reportPosition",
                 String(Math.round(g.x)) + "," + String(Math.round(g.y)));
    }
    function setup(w) {
        if (ours(w, CFG.overlayCaption)) {  // break overlay: above everything, out of the way
            w.keepAbove = true;
            w.skipTaskbar = true;
            w.skipPager = true;
            w.skipSwitcher = true;
            return;
        }
        if (!ours(w, CFG.caption)) return;
        var id = String(w.internalId);
        if (handled.indexOf(id) !== -1) return;
        handled.push(id);
        w.keepAbove = CFG.keepAbove;
        w.skipTaskbar = true;
        w.skipPager = true;
        w.skipSwitcher = true;
        if (CFG.place) {
            var g = w.frameGeometry;
            w.frameGeometry = {x: CFG.x, y: CFG.y, width: g.width, height: g.height};
        }
        w.frameGeometryChanged.connect(function () { report(w); });
        report(w);
    }
    var list = windows();
    for (var i = 0; i < list.length; i++) setup(list[i]);
    (workspace.windowAdded || workspace.clientAdded).connect(setup);
})();
"""


class KWinIntegration(QObject):
    """Loads a small KWin script that manages our window. Fails soft: if
    anything is missing, available() is False and callers fall back.
    Position reports arrive through remote.PetService.reportPosition."""

    def __init__(self, app_id: str, caption: str, parent: QObject | None = None):
        super().__init__(parent)
        self.app_id = app_id
        self.caption = caption
        self._bus = None
        self._script_file: Path | None = None
        self._loaded = False
        try:
            from PySide6.QtDBus import QDBusConnection, QDBusInterface

            self._QDBusInterface = QDBusInterface
            bus = QDBusConnection.sessionBus()
            if bus.isConnected():
                self._bus = bus
        except ImportError:
            log.info("QtDBus not available; KWin integration disabled")

    def _kwin(self, path: str = "/Scripting", iface: str = "org.kde.kwin.Scripting"):
        if self._bus is None:
            return None
        proxy = self._QDBusInterface("org.kde.KWin", path, iface, self._bus)
        return proxy if proxy.isValid() else None

    def available(self) -> bool:
        return self._kwin() is not None

    def start(self, position: tuple[int, int] | None, keep_above: bool = True) -> bool:
        scripting = self._kwin()
        if scripting is None:
            log.warning("KWin scripting D-Bus interface not reachable")
            return False

        cfg = {
            "appId": self.app_id,
            "caption": self.caption,
            "overlayCaption": OVERLAY_TITLE,
            "keepAbove": bool(keep_above),
            "place": position is not None,
            "x": position[0] if position else 0,
            "y": position[1] if position else 0,
            "service": DBUS_SERVICE,
            "path": DBUS_PATH,
            "iface": DBUS_IFACE,
        }
        # KWin reads the file asynchronously, so it is kept until stop().
        fd, name = tempfile.mkstemp(prefix="pomodoro-pet-", suffix=".js", dir=os.environ.get("XDG_RUNTIME_DIR"))
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(_KWIN_SCRIPT.replace("__CONFIG__", json.dumps(cfg)))
        self._script_file = Path(name)

        scripting.call("unloadScript", KWIN_PLUGIN)  # leftover from a crashed run
        reply = scripting.call("loadScript", str(self._script_file), KWIN_PLUGIN)
        args = reply.arguments()
        script_id = args[0] if args else -1
        if not isinstance(script_id, int) or script_id < 0:
            log.warning("KWin refused to load the integration script: %s", reply.errorMessage())
            self._cleanup_file()
            return False
        self._loaded = True

        # Plasma 6 / 5.27 expose /Scripting/Script<N>; older Plasma used /<N>.
        for path in (f"/Scripting/Script{script_id}", f"/{script_id}"):
            script = self._kwin(path, "org.kde.kwin.Script")
            if script is not None:
                script.call("run")
                break
        else:
            scripting.call("start")
        log.info("KWin integration script loaded (id %s)", script_id)
        return True

    def stop(self) -> None:
        if self._loaded:
            scripting = self._kwin()
            if scripting is not None:
                scripting.call("unloadScript", KWIN_PLUGIN)
            self._loaded = False
        self._cleanup_file()

    def _cleanup_file(self) -> None:
        if self._script_file is not None:
            try:
                self._script_file.unlink()
            except OSError:
                pass
            self._script_file = None
