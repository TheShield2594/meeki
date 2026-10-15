"""Stand-in for the bits of Plasma the app talks to, for test_integration_dbus:
the notification server and the screensaver (idle time / lock state).

Tests steer the screensaver with SetIdle(ms) and SetLocked(bool) over D-Bus.

Implements Inhibit like Plasma does: the inhibition belongs to the calling
D-Bus connection and ends when that connection goes away. Writes events to
the log file given as argv[1].

QDBusContext.message() crashes under PySide6, so the caller is taken to be
the newest connection on the bus. That holds here because the app opens a
fresh connection right before each Inhibit call."""

import sys

from PySide6.QtCore import ClassInfo, QCoreApplication, QObject, Signal, Slot
from PySide6.QtDBus import QDBusConnection, QDBusServiceWatcher

app = QCoreApplication([])
log = open(sys.argv[1], "w", buffering=1)  # noqa: SIM115


@ClassInfo({"D-Bus Interface": "org.freedesktop.Notifications"})
class Server(QObject):
    ActionInvoked = Signal("uint", str)
    NotificationClosed = Signal("uint", "uint")
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

    last_id = 0

    @Slot(str, "uint", str, str, str, "QStringList", "QVariantMap", int, result="uint")
    def Notify(self, *args):  # noqa: N802
        Server.last_id += 1
        actions = args[5][0::2]  # [key, label, key, label, ...]
        log.write(f"notify {args[3]}\n" + (f"actions {' '.join(actions)}\n" if actions else ""))
        return Server.last_id

    @Slot(result="QStringList")
    def GetCapabilities(self):  # noqa: N802
        return ["actions", "body"]

    @Slot("QDBusMessage")
    def GetServerInformation(self, msg):  # noqa: N802 (four out-args: reply by hand)
        msg.setDelayedReply(True)
        reply = msg.createReply()
        reply.setArguments(["fake", "test", "1.0", "1.2"])
        bus.send(reply)

    @Slot("uint")
    def CloseNotification(self, nid):  # noqa: N802
        pass

    @Slot(str)
    def InvokeLast(self, action):  # noqa: N802 (test hook: "click" a button on the last notification)
        self.ActionInvoked.emit(Server.last_id, action)
        self.NotificationClosed.emit(Server.last_id, 2)

    def _gone(self, service):
        self.watcher.removeWatchedService(service)
        log.write("released\n")


@ClassInfo({"D-Bus Interface": "org.freedesktop.ScreenSaver"})
class ScreenSaver(QObject):
    def __init__(self):
        super().__init__()
        self.idle_ms = 0
        self.locked = False

    @Slot(result="uint")
    def GetSessionIdleTime(self):  # noqa: N802
        return self.idle_ms

    @Slot(result=bool)
    def GetActive(self):  # noqa: N802
        return self.locked

    @Slot("qlonglong")
    def SetIdle(self, ms):  # noqa: N802
        self.idle_ms = int(ms)

    @Slot(bool)
    def SetLocked(self, locked):  # noqa: N802
        self.locked = locked


bus = QDBusConnection.sessionBus()
screensaver = ScreenSaver()
bus.registerObject("/ScreenSaver", screensaver, QDBusConnection.ExportAllSlots)
bus.registerService("org.freedesktop.ScreenSaver")
server = Server(bus)
bus.registerObject(
    "/org/freedesktop/Notifications", server, QDBusConnection.ExportAllSlots | QDBusConnection.ExportAllSignals
)
bus.registerService("org.freedesktop.Notifications")
log.write("ready\n")
app.exec()
