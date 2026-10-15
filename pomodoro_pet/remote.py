"""Remote control: `pomodoro-pet --toggle` etc., and global shortcuts.

On Linux the running app owns io.github.pomodoropet on the session bus and
exports /PomodoroPet with these methods:

    Toggle, Start, Pause, Skip, Reset, Show,
    Settings                                  -> ()
    Status                                    -> s
    SetIntention(s)                           what you're focusing on
    reportPosition(s), reportFullscreen(s)    used by the KWin script

The KDE global shortcuts (see packaging/pomodoro-pet.desktop, installed into
kglobalaccel/) simply run `pomodoro-pet --toggle` and friends.

Where there's no session bus (macOS), the same methods (except the KWin
ones) are served on a local socket in the runtime directory: the client
sends one JSON line ["Method", args...] and gets {"ok": ..., "result": ...}.
The socket is also started next to D-Bus, as a fallback.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from collections.abc import Callable

from PySide6.QtCore import ClassInfo, QObject, Signal, Slot

from .config import APP_ID, runtime_dir

log = logging.getLogger(__name__)

DBUS_SERVICE = "io.github.pomodoropet"
DBUS_PATH = "/PomodoroPet"
DBUS_IFACE = "io.github.pomodoropet.Pet"

COMMANDS = ("toggle", "start", "pause", "skip", "reset", "show", "settings", "status")
# Methods the local socket serves (the KWin reports only make sense over D-Bus).
LOCAL_METHODS = ("Toggle", "Start", "Pause", "Skip", "Reset", "Show", "Settings", "Status", "SetIntention")


def local_socket_path() -> str:
    return os.path.join(runtime_dir(), f"{APP_ID}.sock")


@ClassInfo({"D-Bus Interface": DBUS_IFACE})
class PetService(QObject):
    command = Signal(str)  # one of COMMANDS except "status"
    position_reported = Signal(int, int)
    intention_set = Signal(str)
    fullscreen_changed = Signal(bool)  # a fullscreen window covers the pet (KWin script)

    def __init__(self, status_provider: Callable[[], str], parent: QObject | None = None):
        super().__init__(parent)
        self._status = status_provider
        self._bus = None
        self._server = None

    def register(self) -> bool:
        local = self._listen_local()
        return self._register_dbus() or local

    def _register_dbus(self) -> bool:
        try:
            from PySide6.QtDBus import QDBusConnection
        except ImportError:
            return False
        bus = QDBusConnection.sessionBus()
        if not bus.isConnected():
            if sys.platform.startswith("linux"):
                log.warning("No D-Bus session bus: KWin integration disabled, remote control uses a local socket")
            return False
        if not bus.registerService(DBUS_SERVICE):
            log.warning("D-Bus name %s is taken; remote control disabled", DBUS_SERVICE)
            return False
        bus.registerObject(DBUS_PATH, self, QDBusConnection.ExportAllSlots)
        self._bus = bus
        return True

    def unregister(self) -> None:
        if self._bus is not None:
            self._bus.unregisterObject(DBUS_PATH)
            self._bus.unregisterService(DBUS_SERVICE)
            self._bus = None
        if self._server is not None:
            self._server.close()
            self._server = None

    # ---- local socket ----------------------------------------------------
    def _listen_local(self) -> bool:
        try:
            from PySide6.QtNetwork import QLocalServer
        except ImportError:
            return False
        server = QLocalServer(self)
        server.setSocketOptions(QLocalServer.UserAccessOption)
        path = local_socket_path()
        if not server.listen(path):
            # We hold the single-instance lock, so this is a leftover from a crash.
            QLocalServer.removeServer(path)
            if not server.listen(path):
                log.warning("Can't listen on %s: %s", path, server.errorString())
                return False
        server.newConnection.connect(self._on_local_connection)
        self._server = server
        return True

    def _on_local_connection(self) -> None:
        while self._server is not None and self._server.hasPendingConnections():
            sock = self._server.nextPendingConnection()
            sock.readyRead.connect(lambda s=sock: self._serve_local(s))
            sock.disconnected.connect(sock.deleteLater)

    def _serve_local(self, sock) -> None:
        if not sock.canReadLine():
            return
        line = bytes(sock.readLine()).decode(errors="replace")
        try:
            method, *args = json.loads(line)
            if method not in LOCAL_METHODS or not all(isinstance(a, str) for a in args):
                raise ValueError(f"unknown method {method!r}")
            reply = {"ok": True, "result": getattr(self, method)(*args) or ""}
        except (ValueError, TypeError) as exc:
            reply = {"ok": False, "error": str(exc)}
        sock.write((json.dumps(reply) + "\n").encode())
        sock.flush()
        sock.disconnectFromServer()

    # ---- exported methods (names are the D-Bus method names) -----------
    @Slot()
    def Toggle(self) -> None:  # noqa: N802
        self.command.emit("toggle")

    @Slot()
    def Start(self) -> None:  # noqa: N802
        self.command.emit("start")

    @Slot()
    def Pause(self) -> None:  # noqa: N802
        self.command.emit("pause")

    @Slot()
    def Skip(self) -> None:  # noqa: N802
        self.command.emit("skip")

    @Slot()
    def Reset(self) -> None:  # noqa: N802
        self.command.emit("reset")

    @Slot()
    def Show(self) -> None:  # noqa: N802
        self.command.emit("show")

    @Slot()
    def Settings(self) -> None:  # noqa: N802
        self.command.emit("settings")

    @Slot(result=str)
    def Status(self) -> str:  # noqa: N802
        return self._status()

    @Slot(str)
    def SetIntention(self, text: str) -> None:  # noqa: N802
        self.intention_set.emit(text.strip()[:120])

    @Slot(str)
    def reportFullscreen(self, flag: str) -> None:  # noqa: N802
        self.fullscreen_changed.emit(flag == "1")

    @Slot(str)
    def reportPosition(self, xy: str) -> None:  # noqa: N802
        try:
            x, y = (int(float(v)) for v in xy.split(","))
        except ValueError:
            return
        self.position_reported.emit(x, y)


_METHODS = {"setintention": "SetIntention"}


def send_command(command: str, *args: str) -> tuple[bool, str]:
    """Client side: call the running instance over D-Bus, or the local
    socket where there's no D-Bus. Needs a QCoreApplication."""
    method = _METHODS.get(command, command.capitalize())
    try:
        from PySide6.QtDBus import QDBusConnection, QDBusInterface, QDBusMessage
    except ImportError:
        return _send_local(method, args)
    bus = QDBusConnection.sessionBus()
    if not bus.isConnected():
        return _send_local(method, args)
    iface = QDBusInterface(DBUS_SERVICE, DBUS_PATH, DBUS_IFACE, bus)
    if not iface.isValid():
        ok, reply = _send_local(method, args)
        return (True, reply) if ok else (False, "Pomodoro Pet is not reachable over D-Bus")
    reply = iface.call(method, *args)
    if reply.type() != QDBusMessage.ReplyMessage:
        return False, reply.errorMessage()
    args = reply.arguments()
    return True, str(args[0]) if args else ""


def _send_local(method: str, args: tuple[str, ...]) -> tuple[bool, str]:
    try:
        from PySide6.QtNetwork import QLocalSocket
    except ImportError:
        return False, "neither QtDBus nor QtNetwork is available"
    sock = QLocalSocket()
    sock.connectToServer(local_socket_path())
    if not sock.waitForConnected(2000):
        return False, f"Pomodoro Pet is not reachable: {sock.errorString()}"
    sock.write((json.dumps([method, *args]) + "\n").encode())
    sock.flush()
    data = b""
    while not data.endswith(b"\n") and sock.waitForReadyRead(5000):
        data += bytes(sock.readAll())
    data += bytes(sock.readAll())
    sock.abort()
    try:
        reply = json.loads(data.decode(errors="replace"))
    except ValueError:
        return False, "no answer from Pomodoro Pet"
    if not reply.get("ok"):
        return False, str(reply.get("error", "error"))
    return True, str(reply.get("result", ""))
