"""Application wiring: config -> timer -> pet window, popover, notifications."""

from __future__ import annotations

import argparse
import logging
import signal
import sys

from . import __version__
from .config import APP_ID, Config
from .wayland import KWinIntegration, choose_platform

log = logging.getLogger("pomodoro_pet")


class Controller:
    def __init__(self, app, cfg: Config):
        # Qt imports happen after choose_platform() has run.
        from PySide6.QtCore import QTimer

        from .assets import load_sprites
        from .pet import WINDOW_TITLE, PetWindow
        from .popover import Popover
        from .timer import PomodoroTimer
        from .wayland import running_on_wayland

        self.app = app
        self.cfg = cfg
        self.timer = PomodoroTimer(
            cfg["focus_minutes"], cfg["short_break_minutes"], cfg["long_break_minutes"],
            cfg["rounds"], cfg["auto_advance"],
        )
        self.pet = PetWindow(self.timer, load_sprites(cfg["sprite_dir"]), cfg["scale"])
        self.popover = Popover(self.timer, self.pet, show_minimize=cfg["minimize_to_tray"])
        self.popover.load(
            cfg["focus_minutes"], cfg["short_break_minutes"], cfg["long_break_minutes"],
            cfg["rounds"], cfg["notifications"], cfg["sound"],
        )
        self.tray = None

        self.pet.clicked.connect(lambda: self.popover.popup_near(self.pet, self.pet.interaction_rect()))
        self.pet.quit_requested.connect(self.quit)
        self.pet.moved.connect(self._remember_position)
        self.popover.settings_changed.connect(self._on_settings)
        self.popover.toggles_changed.connect(self._on_toggles)
        self.popover.minimize_requested.connect(self.minimize_to_tray)
        self.timer.phase_finished.connect(self._on_phase_finished)
        self.timer.session_complete.connect(lambda: self.pet.celebrate(12))

        self._save_debounce = QTimer(singleShot=True, interval=1500)
        self._save_debounce.timeout.connect(self.cfg.save)

        self.kwin = None
        position = cfg["position"]
        if running_on_wayland():
            if cfg["kwin_integration"]:
                self.kwin = KWinIntegration(APP_ID, WINDOW_TITLE)
                self.kwin.position_reported.connect(self._remember_position)
            self.pet.show()
            if self.kwin is not None:
                pos = (position["x"], position["y"]) if position else self._default_position()
                # Defer until the surface is mapped and KWin knows about it.
                QTimer.singleShot(0, lambda: self._start_kwin(pos))
            else:
                log.warning(
                    "Native Wayland without KWin integration: keep-above and position memory "
                    "need the KWin window rule (packaging/kwin) or backend \"xcb\""
                )
        else:
            x, y = (position["x"], position["y"]) if position else self._default_position()
            self.pet.move(self._clamp_to_screens(x, y))
            self.pet.show()

    # ---- positioning ---------------------------------------------------
    def _default_position(self) -> tuple[int, int]:
        screen = self.app.primaryScreen()
        if screen is None:
            return 100, 100
        area = screen.availableGeometry()
        return area.right() - self.pet.width() - 40, area.bottom() - self.pet.height() - 40

    def _clamp_to_screens(self, x: int, y: int):
        from PySide6.QtCore import QPoint, QRect

        rect = QRect(x, y, self.pet.width(), self.pet.height())
        for screen in self.app.screens():
            if screen.availableGeometry().intersects(rect.adjusted(20, 20, -20, -20)):
                return QPoint(x, y)
        return QPoint(*self._default_position())  # saved spot is on a monitor that's gone

    def _start_kwin(self, pos: tuple[int, int]) -> None:
        if not self.kwin.start(pos, keep_above=True):
            log.warning(
                "KWin integration unavailable. For keep-above/position memory on Wayland, import "
                "packaging/kwin/pomodoro-pet.kwinrule or set \"backend\": \"xcb\" in the config."
            )

    def _remember_position(self, x: int, y: int) -> None:
        if self.cfg["position"] != {"x": x, "y": y}:
            self.cfg["position"] = {"x": x, "y": y}
            self._save_debounce.start()

    # ---- settings ------------------------------------------------------
    def _on_settings(self, focus: int, short: int, long: int, rounds: int) -> None:
        self.cfg["focus_minutes"], self.cfg["short_break_minutes"] = focus, short
        self.cfg["long_break_minutes"], self.cfg["rounds"] = long, rounds
        self.timer.configure(focus, short, long, rounds)
        self.pet.update()
        self._save_debounce.start()

    def _on_toggles(self, notifications: bool, sound: bool) -> None:
        self.cfg["notifications"], self.cfg["sound"] = notifications, sound
        self._save_debounce.start()

    # ---- phase events --------------------------------------------------
    def _on_phase_finished(self, finished, nxt) -> None:
        from .notify import notify, play_sound
        from .timer import Phase

        if finished is Phase.FOCUS:
            self.pet.celebrate(4)
        if nxt is None:
            summary, body = "Pomodoro session complete! 🎉", "All rounds done. Good dog, good human."
        elif nxt is Phase.FOCUS:
            summary, body = "Break's over", f"Time to focus — round {self.timer.round + 1}/{self.timer.rounds}."
        elif nxt is Phase.LONG_BREAK:
            summary, body = "Focus round done!", f"Enjoy a {self.timer.long_break_minutes}-minute long break."
        else:
            summary, body = "Focus round done!", f"Take a {self.timer.short_break_minutes}-minute break."
        if not self.timer.auto_advance and nxt is not None:
            body += " Click the pup to start."
        if self.cfg["notifications"]:
            notify(summary, body)
        if self.cfg["sound"]:
            play_sound(self.cfg["sound_file"])

    # ---- tray (experimental stub) --------------------------------------
    def minimize_to_tray(self) -> None:
        from .tray import TrayController

        if not TrayController.available():
            log.warning("No system tray available")
            return
        if self.tray is None:
            self.tray = TrayController(self.timer)
            self.tray.restore_requested.connect(self.pet.show)
        self.pet.hide()
        self.tray.show()

    # ---- lifecycle -----------------------------------------------------
    def quit(self) -> None:
        self.shutdown()
        self.app.quit()

    def shutdown(self) -> None:
        if getattr(self, "_shut_down", False):
            return
        self._shut_down = True
        self._save_debounce.stop()
        self.cfg.save()
        if self.kwin is not None:
            self.kwin.stop()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog=APP_ID, description="A Pomodoro desktop pet (Bichon x Biewer pup).")
    parser.add_argument("--backend", choices=("auto", "wayland", "xcb"), help="override the config's backend")
    parser.add_argument("--reset-position", action="store_true", help="forget the saved window position")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(name)s: %(message)s")

    cfg = Config().load()
    if args.reset_position:
        cfg["position"] = None
    choose_platform(args.backend or cfg["backend"])

    from PySide6.QtCore import QDir, QLockFile, QStandardPaths
    from PySide6.QtGui import QGuiApplication, QIcon
    from PySide6.QtWidgets import QApplication

    from .assets import asset

    # One pet at a time: launching again from the menu shouldn't spawn a twin.
    runtime_dir = QStandardPaths.writableLocation(QStandardPaths.RuntimeLocation) or QDir.tempPath()
    lock = QLockFile(f"{runtime_dir}/{APP_ID}.lock")
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        log.error("Pomodoro Pet is already running")
        return 1

    QGuiApplication.setDesktopFileName(APP_ID)  # Wayland app_id -> matches the .desktop file
    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_ID)  # X11 WM_CLASS
    app.setApplicationDisplayName("Pomodoro Pet")
    app.setApplicationVersion(__version__)
    app.setQuitOnLastWindowClosed(False)
    icon = QIcon.fromTheme(APP_ID)
    if icon.isNull() and asset("icons", "pomodoro-pet.png"):
        icon = QIcon(str(asset("icons", "pomodoro-pet.png")))
    app.setWindowIcon(icon)
    log.info("Qt platform: %s", app.platformName())

    controller = Controller(app, cfg)

    # Save and clean up on Ctrl+C / SIGTERM too. A no-op timer lets Python
    # handle signals while Qt's event loop is running.
    from PySide6.QtCore import QTimer

    signal.signal(signal.SIGINT, lambda *_: controller.quit())
    signal.signal(signal.SIGTERM, lambda *_: controller.quit())
    keepalive = QTimer(interval=300)
    keepalive.timeout.connect(lambda: None)
    keepalive.start()
    app.aboutToQuit.connect(controller.shutdown)

    return app.exec()
