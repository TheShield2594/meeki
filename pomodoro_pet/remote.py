"""D-Bus remote control: `pomodoro-pet --toggle` etc., and global shortcuts.

The running app owns io.github.pomodoropet on the session bus and exports
/PomodoroPet with these methods:

    Toggle, Start, Pause, Skip, Reset, Show   -> ()
    Status                                    -> s
    reportPosition(s)                         used by the KWin script

The KDE global shortcuts (see packaging/pomodoro-pet.desktop, installed into
kglobalaccel/) simply run `pomodoro-pet --toggle` and friends.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtCore import ClassInfo, QObject, Signal, Slot

log = logging.getLogger(__name__)

DBUS_SERVICE = "io.github.pomodoropet"
DBUS_PATH = "/PomodoroPet"
DBUS_IFACE = "io.github.pomodoropet.Pet"

COMMANDS = ("toggle", "start", "pause", "skip", "reset", "show", "status")


@ClassInfo({"D-Bus Interface": DBUS_IFACE})
class PetService(QObject):
    command = Signal(str)  # one of COMMANDS except "status"
    position_reported = Signal(int, int)

    def __init__(self, status_provider: Callable[[], str], parent: QObject | None = None):
        super().__init__(parent)
        self._status = status_provider
        self._bus = None

    def register(self) -> bool:
        try:
            from PySide6.QtDBus import QDBusConnection
        except ImportError:
            return False
        bus = QDBusConnection.sessionBus()
        if not bus.isConnected():
            log.warning("No D-Bus session bus: remote control and KWin position memory disabled")
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

    @Slot(result=str)
    def Status(self) -> str:  # noqa: N802
        return self._status()

    @Slot(str)
    def reportPosition(self, xy: str) -> None:  # noqa: N802
        try:
            x, y = (int(float(v)) for v in xy.split(","))
        except ValueError:
            return
        self.position_reported.emit(x, y)


def send_command(command: str) -> tuple[bool, str]:
    """Client side: call the running instance. Needs a QCoreApplication."""
    try:
        from PySide6.QtDBus import QDBusConnection, QDBusInterface, QDBusMessage
    except ImportError:
        return False, "QtDBus is not available"
    bus = QDBusConnection.sessionBus()
    if not bus.isConnected():
        return False, "no D-Bus session bus"
    iface = QDBusInterface(DBUS_SERVICE, DBUS_PATH, DBUS_IFACE, bus)
    if not iface.isValid():
        return False, "Pomodoro Pet is not reachable over D-Bus"
    reply = iface.call(command.capitalize())
    if reply.type() != QDBusMessage.ReplyMessage:
        return False, reply.errorMessage()
    args = reply.arguments()
    return True, str(args[0]) if args else ""
