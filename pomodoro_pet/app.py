"""Application wiring: config -> timer -> pet window, popover, notifications."""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import tempfile

from . import __version__
from .config import APP_ID, Config, load_session, save_session
from .remote import COMMANDS, PetService, send_command
from .timer import Phase, PomodoroTimer, State, format_mmss
from .wayland import KWinIntegration, choose_platform

log = logging.getLogger("pomodoro_pet")


class Controller:
    def __init__(self, app, cfg: Config, autostart: bool = False):
        # Qt imports happen after choose_platform() has run.
        from PySide6.QtCore import QTimer

        from .assets import load_sprites
        from .dnd import DoNotDisturb
        from .pet import WINDOW_TITLE, PetWindow
        from .popover import Popover
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
            cfg["rounds"], cfg["notifications"], cfg["sound"], cfg["dnd_during_focus"],
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
        self.timer.state_changed.connect(self._on_timer_changed)
        self.timer.phase_changed.connect(self._on_timer_changed)

        self.dnd = DoNotDisturb()
        self.service = PetService(self.status_text)
        self.service.command.connect(self._on_command)
        self.service.position_reported.connect(self._remember_position)
        self.service.register()

        self._save_debounce = QTimer(singleShot=True, interval=1500)
        self._save_debounce.timeout.connect(self.cfg.save)

        self.kwin = None
        position = cfg["position"]
        if running_on_wayland():
            if cfg["kwin_integration"]:
                self.kwin = KWinIntegration(APP_ID, WINDOW_TITLE)
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

        # Pick up a session that was running when the app last quit (or crashed).
        self._restoring = True
        if self.timer.restore(load_session()):
            log.info("Resumed session: %s", self.status_text())
        self._restoring = False
        if autostart and self.timer.state is not State.RUNNING:
            self.timer.start()
        self._on_timer_changed()

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

    def _on_toggles(self, notifications: bool, sound: bool, dnd: bool) -> None:
        self.cfg["notifications"], self.cfg["sound"] = notifications, sound
        self.cfg["dnd_during_focus"] = dnd
        self._update_dnd()
        self._save_debounce.start()

    # ---- timer state ---------------------------------------------------
    def _on_timer_changed(self, *_args) -> None:
        self._update_dnd()
        if not getattr(self, "_restoring", False):
            save_session(self.timer.snapshot())

    def _update_dnd(self) -> None:
        t = self.timer
        self.dnd.set_active(
            self.cfg["dnd_during_focus"] and t.state is State.RUNNING and t.phase is Phase.FOCUS
        )

    def status_text(self) -> str:
        t = self.timer
        if t.state is State.COMPLETE:
            return "Session complete"
        left = format_mmss(t.remaining_seconds())
        if t.state is State.IDLE:
            return f"Idle · {left}"
        text = t.phase.label
        if t.phase is Phase.FOCUS:
            text += f" · round {t.round}/{t.rounds}"
        text += f" · {left} left"
        return text + (" (paused)" if t.state is State.PAUSED else "")

    def _on_command(self, command: str) -> None:
        t = self.timer
        actions = {"toggle": t.toggle, "start": t.start, "pause": t.pause, "skip": t.skip, "reset": t.reset}
        if command in actions:
            actions[command]()
        elif command == "show":
            if self.tray is not None:
                self.tray.hide()
            self.pet.show()
            self.pet.raise_()

    # ---- phase events --------------------------------------------------
    def _on_phase_finished(self, finished, nxt) -> None:
        from .notify import notify, play_sound

        if finished is Phase.FOCUS:
            self.pet.celebrate(4)
            # Lift Do Not Disturb first, or our own "break time" notice gets swallowed.
            self.dnd.set_active(False)
        away = self.timer.away_seconds
        if away and nxt is not None:
            mins = max(1, round(away / 60))
            what = "Focus round" if finished is Phase.FOCUS else "Break"
            summary = f"Welcome back! {what} ended {mins} min ago"
            body = f"Paused at the start of the {nxt.label.lower()}. Click the pup or press your shortcut to continue."
        elif nxt is None:
            summary, body = "Pomodoro session complete! 🎉", "All rounds done. Good dog, good human."
        elif nxt is Phase.FOCUS:
            summary, body = "Break's over", f"Time to focus — round {self.timer.round + 1}/{self.timer.rounds}."
        elif nxt is Phase.LONG_BREAK:
            summary, body = "Focus round done!", f"Enjoy a {self.timer.long_break_minutes}-minute long break."
        else:
            summary, body = "Focus round done!", f"Take a {self.timer.short_break_minutes}-minute break."
        if not self.timer.auto_advance and nxt is not None and not away:
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
        save_session(self.timer.snapshot())
        self.dnd.set_active(False)
        self.service.unregister()
        if self.kwin is not None:
            self.kwin.stop()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog=APP_ID, description="A Pomodoro desktop pet (Bichon x Biewer pup).")
    remote = parser.add_argument_group("control a running pet (or start one)")
    for cmd in COMMANDS:
        helps = {
            "toggle": "start or pause the timer", "start": "start/resume the timer", "pause": "pause the timer",
            "skip": "end the current phase now", "reset": "reset the session", "show": "show the pet",
            "status": "print the current phase and time left",
        }
        remote.add_argument(f"--{cmd}", dest="command", action="store_const", const=cmd, help=helps[cmd])
    parser.add_argument("--backend", choices=("auto", "wayland", "xcb"), help="override the config's backend")
    parser.add_argument("--reset-position", action="store_true", help="forget the saved window position")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(name)s: %(message)s")

    # One pet at a time. If one is already running, this invocation is a remote
    # control: pass the command (or "show") to it over D-Bus and exit.
    from PySide6.QtCore import QLockFile

    runtime_dir = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
    lock = QLockFile(os.path.join(runtime_dir, f"{APP_ID}.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        from PySide6.QtCore import QCoreApplication

        _core = QCoreApplication(sys.argv[:1])  # noqa: F841 (needed for D-Bus)
        command = args.command or "show"
        ok, reply = send_command(command)
        if not ok:
            log.error("Pomodoro Pet seems to be running but didn't answer: %s", reply)
            return 1
        if command == "status":
            print(reply)
        elif not args.command:
            log.info("Pomodoro Pet is already running")
        return 0
    if args.command in ("status", "pause", "skip", "reset"):
        print("Pomodoro Pet is not running")
        return 1

    cfg = Config().load()
    if args.reset_position:
        cfg["position"] = None
    choose_platform(args.backend or cfg["backend"])

    from PySide6.QtGui import QGuiApplication, QIcon
    from PySide6.QtWidgets import QApplication

    from .assets import asset

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

    controller = Controller(app, cfg, autostart=args.command in ("start", "toggle"))

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
