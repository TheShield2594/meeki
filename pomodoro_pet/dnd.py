"""Plasma "Do Not Disturb" while a focus round is running.

Uses org.freedesktop.Notifications.Inhibit, which Plasma implements (it's what
the "Do not disturb" mode in the notification applet uses). Plasma ends an
inhibition when the D-Bus connection that requested it disappears, so we hold
it on a dedicated connection and simply close that connection to release it.
That also means a crash can never leave Do Not Disturb stuck on. (PySide6
can't marshal the uint32 cookie UnInhibit wants, so this is also the only
reliable way to release it from Python.)
"""

from __future__ import annotations

import logging

from .config import APP_ID
from .i18n import _

log = logging.getLogger(__name__)

_CONNECTION = "pomodoro-pet-dnd"


class DoNotDisturb:
    def __init__(self) -> None:
        self.active = False
        self._warned = False

    def set_active(self, on: bool) -> None:
        if on and not self.active:
            self.active = self._inhibit()
        elif not on and self.active:
            self._release()

    def _inhibit(self) -> bool:
        try:
            from PySide6.QtDBus import QDBusConnection, QDBusInterface, QDBusMessage
        except ImportError:
            return False
        bus = QDBusConnection.connectToBus(QDBusConnection.SessionBus, _CONNECTION)
        ok = False
        if bus.isConnected():
            iface = QDBusInterface(
                "org.freedesktop.Notifications", "/org/freedesktop/Notifications",
                "org.freedesktop.Notifications", bus,
            )
            reply = iface.call("Inhibit", APP_ID, _("Focus round in progress"), {})
            ok = reply.type() == QDBusMessage.ReplyMessage
            if not ok and not self._warned:
                self._warned = True
                log.info("Notification server doesn't support Do Not Disturb: %s", reply.errorMessage())
            del iface
        del bus  # drop our reference so disconnectFromBus can really close it
        if not ok:
            QDBusConnection.disconnectFromBus(_CONNECTION)
        else:
            log.debug("Do Not Disturb on")
        return ok

    def _release(self) -> None:
        from PySide6.QtDBus import QDBusConnection

        QDBusConnection.disconnectFromBus(_CONNECTION)
        self.active = False
        log.debug("Do Not Disturb off")
