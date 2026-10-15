"""Application wiring: config -> timer -> pet window, popover, notifications."""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import tempfile
import time

from . import __version__
from .config import APP_ID, Config, load_session, save_session
from .history import History, format_minutes, mood, recent_intentions, stats_text, summarize
from .remote import COMMANDS, PetService, send_command
from .timer import Phase, PomodoroTimer, State, format_mmss
from .wayland import KWinIntegration, choose_platform

log = logging.getLogger("pomodoro_pet")


class Controller:
    def __init__(self, app, cfg: Config, autostart: bool = False, intention: str | None = None):
        # Qt imports happen after choose_platform() has run.
        from PySide6.QtCore import QTimer

        from .assets import load_sprites
        from .break_overlay import BreakOverlay
        from .dnd import DoNotDisturb
        from .idle import ActivityTracker, BreakCoach, IdleSource
        from .pet import WINDOW_TITLE, PetWindow
        from .popover import Popover
        from .wayland import running_on_wayland

        self.app = app
        self.cfg = cfg
        self.intention = ""
        self.history = History().load()
        self.stats_window = None
        self.timer = PomodoroTimer(
            cfg["focus_minutes"], cfg["short_break_minutes"], cfg["long_break_minutes"],
            cfg["rounds"], cfg["auto_advance"],
        )
        sprites = load_sprites(cfg["sprite_dir"])
        self.pet = PetWindow(self.timer, sprites, cfg["scale"])
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
        self.popover.intention_changed.connect(self.set_intention)
        self.popover.stats_requested.connect(self.show_stats)
        self.popover.load_behavior(cfg["idle_pause"], cfg["break_enforcement"])
        self.popover.behavior_changed.connect(self._on_behavior)
        self.timer.phase_ended.connect(self._on_phase_ended)
        self.timer.tick.connect(self._update_tooltip)
        self.timer.phase_finished.connect(self._on_phase_finished)
        self.timer.session_complete.connect(lambda: self.pet.celebrate(12))
        self.timer.state_changed.connect(self._on_timer_changed)
        self.timer.phase_changed.connect(self._on_timer_changed)

        self.dnd = DoNotDisturb()
        self.service = PetService(self.status_text)
        self.service.command.connect(self._on_command)
        self.service.position_reported.connect(self._remember_position)
        self.service.intention_set.connect(self.set_intention)
        self.service.register()

        # Idle detection + break enforcement
        self._auto_paused: float | None = None  # seconds rewound when we paused for you
        self._break_key = None  # (round, phase) of the break being coached
        self.coach = BreakCoach()
        self.overlay = BreakOverlay(sprites["break"], cfg["pet_name"])
        self.overlay.skip_requested.connect(self.timer.skip)
        self.overlay.snoozed.connect(lambda n: self.say("Okay, one more minute… 🐾"))
        self.timer.tick.connect(self.overlay.set_remaining)
        poll = float(os.environ.get("POMODORO_PET_IDLE_POLL", "5"))
        self.activity = ActivityTracker(IdleSource(), away_after=cfg["idle_minutes"] * 60, poll=poll)
        self.activity.away.connect(self._on_away)
        self.activity.back.connect(self._on_back)
        self.activity.activity.connect(self._on_activity)
        self.activity.start()

        self._save_debounce = QTimer(singleShot=True, interval=1500)
        self._save_debounce.timeout.connect(self.cfg.save)
        # Mood drifts with the clock too (new day, days away), not just events.
        self._mood_timer = QTimer(interval=10 * 60 * 1000)
        self._mood_timer.timeout.connect(self._refresh_mood)
        self._mood_timer.start()

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
        session = load_session()
        if self.timer.restore(session):
            self.intention = str(session.get("intention") or "")
            log.info("Resumed session: %s", self.status_text())
        self._restoring = False
        if intention is not None:
            self.intention = intention.strip()[:120]
        self.popover.set_intention(self.intention)
        self._refresh_mood(react=False)
        if self.mood.reason.startswith("hasn't seen you"):
            QTimer.singleShot(1500, lambda: self.say("I missed you! 🥺"))
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
        if not hasattr(self, "coach"):
            return  # still constructing
        if self.timer.state is State.RUNNING:
            self._auto_paused = None  # running again, however it happened
        self._update_break()
        if not getattr(self, "_restoring", False):
            self._save_session()
        self._update_tooltip()

    def _save_session(self) -> None:
        snap = self.timer.snapshot()
        if snap is not None:
            snap["intention"] = self.intention
        save_session(snap)

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
        text += " (paused)" if t.state is State.PAUSED else ""
        if self.intention and t.phase is Phase.FOCUS:
            text += f" — {self.intention}"
        return text

    # ---- intention, history, mood ---------------------------------------
    def set_intention(self, text: str) -> None:
        text = text.strip()[:120]
        if text == self.intention:
            return
        self.intention = text
        self.popover.set_intention(text)
        self._save_session()
        self._update_tooltip()

    def _on_phase_ended(self, phase, seconds: float, outcome: str, round_: int) -> None:
        from PySide6.QtCore import QTimer

        if seconds < 1 and outcome != "completed":
            return  # skipping a phase that never started isn't worth logging
        record = {
            "type": phase.value, "outcome": outcome, "end": time.time(), "seconds": round(seconds), "round": round_,
        }
        if phase is Phase.FOCUS and self.intention:
            record["intention"] = self.intention
        if phase.is_break and self._break_key == (round_, phase):
            record["active_seconds"] = round(self.coach.active_seconds)  # time spent at the computer
        self.history.append(record)

        before = self.mood.level
        self._refresh_mood(react=False)
        if phase is Phase.FOCUS and outcome == "completed":
            n = summarize(self.history.records).today_rounds
            self.say(f"{n} round{'s' if n != 1 else ''} today! Good human 🐾")
        elif phase.is_break and outcome == "skipped":
            self.say("Hey… breaks keep us sharp 😤")
        elif phase is Phase.FOCUS and seconds >= 60:
            self.say("Aww, we were on a roll…")
        if self.mood.level != before and self.mood.is_happy and before not in ("happy", "overjoyed"):
            QTimer.singleShot(5200, lambda: self.say(f"{self.cfg['pet_name']} is {self.mood.level} {self.mood.emoji}"))

    def _refresh_mood(self, react: bool = True) -> None:
        records = self.history.records
        self.mood = mood(records)
        self.pet.set_mood(self.mood.level)
        s = summarize(records)
        summary = f"Today {s.today_rounds} rounds · {format_minutes(s.today_minutes)}"
        if s.streak:
            summary += f" · 🔥 {s.streak}-day streak"
        self.popover.set_stats(f"{self.cfg['pet_name']} is {self.mood.level} {self.mood.emoji}", summary)
        self.popover.set_suggestions(recent_intentions(records))
        if self.stats_window is not None and self.stats_window.isVisible():
            self.stats_window.refresh(records, self.mood)
        self._update_tooltip()

    def _update_tooltip(self, *_args) -> None:
        if not hasattr(self, "mood"):
            return
        lines = []
        if self.intention:
            lines.append(f"🎯 {self.intention}")
        status = self.status_text()
        if self.intention and " — " in status:
            status = status.rsplit(" — ", 1)[0]
        lines.append(status)
        lines.append(f"{self.cfg['pet_name']} is {self.mood.level} {self.mood.emoji} — {self.mood.reason}")
        self.pet.setToolTip("\n".join(lines))

    # ---- idle detection ----------------------------------------------
    def _on_behavior(self, idle_pause: bool, break_mode: str) -> None:
        self.cfg["idle_pause"], self.cfg["break_enforcement"] = idle_pause, break_mode
        if break_mode != "overlay":
            self.overlay.end()
        self._update_break()
        self._save_debounce.start()

    def _on_away(self, idle: float) -> None:
        t = self.timer
        if not self.cfg["idle_pause"] or t.state is not State.RUNNING or t.phase is not Phase.FOCUS:
            return
        rewind = min(idle, t.elapsed())
        t.pause(rewind=rewind)
        self._auto_paused = rewind
        log.info("You stepped away: paused focus (gave back %.0f s of idle time)", rewind)

    def _on_back(self, away: float) -> None:
        from .notify import notify_with_actions

        if self._auto_paused is None or self.timer.state is not State.PAUSED:
            return
        mins = max(1, round(away / 60))
        if self.cfg["idle_auto_resume"]:
            self.timer.start()
            self.say("Welcome back! Picking up where we left off 🐾")
            return
        self.say(f"Welcome back! I paused while you were away ({mins} min) 🐾")
        pause_token = self._auto_paused

        def on_action(choice: str) -> None:
            if self._auto_paused is not pause_token or self.timer.state is not State.PAUSED:
                return  # already resumed some other way
            if choice == "credit":
                self.timer.credit(away)
            self.timer.start()

        notify_with_actions(
            "Welcome back!",
            f"{self.cfg['pet_name']} paused your focus round while you were away for {mins} min.",
            [("resume", "Resume"), ("credit", "I was working, count it")],
            on_action,
        )

    # ---- break enforcement ---------------------------------------------
    def _update_break(self) -> None:
        t = self.timer
        in_break = t.state is State.RUNNING and t.phase.is_break
        key = (t.round, t.phase)
        if in_break and key != self._break_key:
            self._break_key = key
            self.coach.begin()
        overlay_wanted = in_break and self.cfg["break_enforcement"] == "overlay"
        if overlay_wanted and not self.overlay.active:
            self.overlay.begin(t.remaining_seconds())
        elif not overlay_wanted and self.overlay.active:
            self.overlay.end()
        if not t.phase.is_break:
            self._break_key = None

    def _on_activity(self, dt: float, active: bool) -> None:
        from .idle import nudge_text
        from .notify import notify

        t = self.timer
        if not (t.state is State.RUNNING and t.phase.is_break):
            return
        n = self.coach.feed(dt, active)
        mode = self.cfg["break_enforcement"]
        if not n or mode == "off" or (mode == "overlay" and self.overlay.windows):
            return
        self.pet.nudge()
        self.say(nudge_text(n))
        if n == 3 and self.cfg["notifications"]:
            notify("It's break time!", f"{self.cfg['pet_name']} really wants you to step away from the computer.")

    def say(self, text: str) -> None:
        """A short speech-bubble style message from the pup."""
        from PySide6.QtCore import QPoint, QRect
        from PySide6.QtWidgets import QToolTip

        if not self.pet.isVisible():
            return
        anchor = self.pet.mapToGlobal(QPoint(self.pet.bubble_rect.left(), self.pet.bubble_rect.bottom() + 4))
        QToolTip.showText(anchor, text, self.pet, QRect(), 5000)

    def show_stats(self) -> None:
        from .stats_window import StatsWindow

        if self.stats_window is None:
            self.stats_window = StatsWindow(self.cfg["pet_name"])
        self.stats_window.refresh(self.history.records, self.mood)
        self.stats_window.show()
        self.stats_window.raise_()
        self.stats_window.activateWindow()

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
        if finished is Phase.FOCUS and self.intention and not away:
            body = f"“{self.intention}” — {body}"
        if nxt is None:
            s = summarize(self.history.records)
            body += f" Today: {s.today_rounds} rounds, {format_minutes(s.today_minutes)}."
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
        self._save_session()
        self.activity.stop()
        self.overlay.end()
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
    remote.add_argument("--intention", metavar="TEXT", help="set what you're focusing on (combine with --start)")
    parser.add_argument("--stats", action="store_true", help="print focus stats and the pup's mood, then exit")
    parser.add_argument("--backend", choices=("auto", "wayland", "xcb"), help="override the config's backend")
    parser.add_argument("--reset-position", action="store_true", help="forget the saved window position")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(name)s: %(message)s")

    if args.stats:
        cfg = Config().load()
        print(stats_text(History().load().records, cfg["pet_name"]))
        return 0

    # One pet at a time. If one is already running, this invocation is a remote
    # control: pass the command (or "show") to it over D-Bus and exit.
    from PySide6.QtCore import QLockFile

    runtime_dir = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
    lock = QLockFile(os.path.join(runtime_dir, f"{APP_ID}.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        from PySide6.QtCore import QCoreApplication

        _core = QCoreApplication(sys.argv[:1])  # noqa: F841 (needed for D-Bus)
        if args.intention is not None:
            ok, reply = send_command("setintention", args.intention)
            if not ok:
                log.error("Couldn't set the intention: %s", reply)
                return 1
            if not args.command:
                return 0
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

    controller = Controller(app, cfg, autostart=args.command in ("start", "toggle"), intention=args.intention)

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
