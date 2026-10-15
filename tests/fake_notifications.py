"""Stand-in for Plasma's notification server, for test_integration_dbus.

Implements Inhibit like Plasma does: the inhibition belongs to the calling
D-Bus connection and ends when that connection goes away. Writes events to
the log file given as argv[1].

QDBusContext.message() crashes under PySide6, so the caller is taken to be
the newest connection on the bus. That holds here because the app opens a
fresh connection right before each Inhibit call."""

import sys

from PySide6.QtCore import ClassInfo, QCoreApplication, QObject, Slot
from PySide6.QtDBus import QDBusConnection, QDBusServiceWatcher

app = QCoreApplication([])
log = open(sys.argv[1], "w", buffering=1)  # noqa: SIM115


@ClassInfo({"D-Bus Interface": "org.freedesktop.Notifications"})
class Server(QObject):
    def __init__(self, bus):
        super().__init__()
        self.watcher = QDBusServiceWatcher(self)
        self.watcher.setConnection(bus)
        self.watcher.setWatchMode(QDBusServiceWatcher.WatchForUnregistration)
        self.watcher.serviceUnregistered.connect(self._gone)

    @Slot(str, str, "QVariantMap", result="uint")
    def Inhibit(self, desktop_entry, reason, hints):  # noqa: N802
        names = bus.interface().registeredServiceNames().value()
        sender = max((n for n in names if n.startswith(":")), key=lambda n: tuple(map(int, n[1:].split("."))))
        self.watcher.addWatchedService(sender)
        log.write(f"inhibit {desktop_entry}\n")
        return 1

    @Slot(str, "uint", str, str, str, "QStringList", "QVariantMap", int, result="uint")
    def Notify(self, *args):  # noqa: N802
        log.write(f"notify {args[3]}\n")
        return 1

    def _gone(self, service):
        self.watcher.removeWatchedService(service)
        log.write("released\n")


bus = QDBusConnection.sessionBus()
server = Server(bus)
bus.registerObject("/org/freedesktop/Notifications", server, QDBusConnection.ExportAllSlots)
bus.registerService("org.freedesktop.Notifications")
log.write("ready\n")
app.exec()
