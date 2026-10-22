"""Application wiring: config -> timer -> pet window, popover, notifications."""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import time

from . import __version__
from .config import APP_ID, Config, config_dir, load_session, runtime_dir, sanitize, save_session
from .history import History, format_minutes, mood, recent_intentions, stats_text, summarize
from .i18n import _, ngettext
from .remote import COMMANDS, PetService, send_command
from .timer import Phase, PomodoroTimer, State, format_mmss
from .wayland import KWinIntegration, choose_platform, is_kde_session

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
            flow=cfg["flow_mode"], warn_seconds=60 if cfg["minute_warning"] else 0,
        )
        sprites = load_sprites(cfg["sprite_dir"])
        self.pet = PetWindow(self.timer, sprites, cfg["scale"])
        self.popover = Popover(self.timer, self.pet)
        self.popover.load_options(cfg)
        self.tray = None
        self.settings_window = None
        self._started_with = {k: cfg[k] for k in ("backend", "kwin_integration")}

        self.pet.clicked.connect(lambda: self.popover.popup_near(self.pet, self.pet.interaction_rect()))
        self.pet.quit_requested.connect(self.quit)
        self.pet.moved.connect(self._remember_position)
        self.pet.petted.connect(self._on_petted)
        self.pet.scrolled.connect(self._on_scrolled)
        self.pet.menu_requested.connect(self._show_menu)
        self.pet.idle_animations = cfg["idle_animations"]
        self._last_pet_logged = 0.0
        self._last_pet_said = 0.0
        self._pet_hush_round = None
        self._hook_key = None  # (round, phase) whose phase_start hook already ran
        self._hook_prev_state = None
        self.popover.options_changed.connect(self.set_options)
        self.popover.minimize_requested.connect(self.minimize_to_tray)
        self.popover.intention_changed.connect(self.set_intention)
        self.popover.stats_requested.connect(self.show_stats)
        self.popover.settings_requested.connect(self.show_settings)
        self.timer.overtime_started.connect(self._on_overtime_started)
        self.timer.warning.connect(self._on_warning)
        self.timer.phase_ended.connect(self._on_phase_ended)
        self.timer.tick.connect(self._update_tooltip)
        self.timer.phase_finished.connect(self._on_phase_finished)
        self.timer.session_complete.connect(lambda: self.pet.celebrate(12))
        self.timer.session_complete.connect(lambda: self.hook("session_complete"))
        self.timer.state_changed.connect(self._on_timer_changed)
        self.timer.phase_changed.connect(self._on_timer_changed)

        self.dnd = DoNotDisturb()
        self.service = PetService(self.status_text)
        self.service.command.connect(self._on_command)
        self.service.position_reported.connect(self._remember_position)
        self.service.intention_set.connect(self.set_intention)
        self.service.fullscreen_changed.connect(self._on_fullscreen)
        self._fullscreen = False
        self._hidden_for_fullscreen = False
        self.service.register()

        # Idle detection + break enforcement
        self._auto_paused: float | None = None  # seconds rewound when we paused for you
        self._break_key = None  # (round, phase) of the break being coached
        self.coach = BreakCoach()
        self.overlay = BreakOverlay(sprites["break"], cfg["pet_name"], fps=sprites.fps.get("break", 2))
        self.overlay.skip_requested.connect(self.timer.skip)
        self.overlay.snoozed.connect(lambda n: self.say(_("Okay, one more minute… 🐾")))
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
        # Overtime has no end time to save, so keep the session file fresh while it runs.
        self._overtime_saver = QTimer(interval=60 * 1000)
        self._overtime_saver.timeout.connect(lambda: self.timer.in_overtime and self._save_session())
        self._overtime_saver.start()

        self.updates = None
        self._update_checks()

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
            if cfg["kwin_integration"] and is_kde_session():
                # X11 Plasma: Qt places the window itself; KWin still puts it on
                # every desktop and tells us about fullscreen apps.
                self.kwin = KWinIntegration(APP_ID, WINDOW_TITLE)
                QTimer.singleShot(0, lambda: self._start_kwin(None))

        # Pick up a session that was running when the app last quit (or crashed).
        # restore() can end a phase right away; its handlers read the mood and intention.
        self._refresh_mood(react=False)
        self._restoring = True
        session = load_session()
        if session:
            self.intention = str(session.get("intention") or "")[:120]
        if self.timer.restore(session):
            log.info("Resumed session: %s", self.status_text())
        else:
            self.intention = ""
        self._restoring = False
        if intention is not None:
            self.intention = intention.strip()[:120]
        self.popover.set_intention(self.intention)
        self._refresh_mood(react=False)
        if self.mood.away_days >= 2:
            QTimer.singleShot(1500, lambda: self.say(_("I missed you! 🥺")))
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

    def _start_kwin(self, pos: tuple[int, int] | None) -> None:
        if not self.kwin.start(pos, keep_above=True) and pos is not None:
            log.warning(
                "KWin integration unavailable. For keep-above/position memory on Wayland, import "
                "packaging/kwin/pomodoro-pet.kwinrule or set \"backend\": \"xcb\" in the config."
            )

    def _remember_position(self, x: int, y: int) -> None:
        if self.cfg["position"] != {"x": x, "y": y}:
            self.cfg["position"] = {"x": x, "y": y}
            self._save_debounce.start()

    # ---- settings ------------------------------------------------------
    def set_options(self, changes: dict) -> None:
        """The one place settings change (popover, settings window). Values
        are validated like the config file, applied live, then saved."""
        from .assets import load_sprites

        clean = sanitize({**self.cfg.data, **changes})
        changed = {k: clean[k] for k in changes if k in clean and clean[k] != self.cfg[k]}
        if not changed:
            return
        self.cfg.data.update(changed)
        c, t = self.cfg, self.timer
        keys = changed.keys()
        if keys & {"focus_minutes", "short_break_minutes", "long_break_minutes", "rounds"}:
            t.configure(c["focus_minutes"], c["short_break_minutes"], c["long_break_minutes"], c["rounds"])
        t.auto_advance = c["auto_advance"]
        t.flow = c["flow_mode"]
        t.warn_seconds = 60 if c["minute_warning"] else 0
        self.activity.away_after = c["idle_minutes"] * 60
        if "hide_in_fullscreen" in keys:
            self._apply_visibility()
        if "idle_animations" in keys:
            self.pet.set_idle_animations(c["idle_animations"])
        if "scale" in keys:
            self.pet.set_scale(c["scale"])
        if "sprite_dir" in keys:
            sprites = load_sprites(c["sprite_dir"])
            self.pet.set_sprites(sprites)
            self.overlay.set_frames(sprites["break"], sprites.fps.get("break", 2))
        if "pet_name" in keys:
            self.overlay.pet_name = c["pet_name"]
            if self.stats_window is not None:
                self.stats_window.pet_name = c["pet_name"]
            self._refresh_mood(react=False)
        if "check_for_updates" in keys:
            self._update_checks()
        if "break_enforcement" in keys and c["break_enforcement"] != "overlay":
            self.overlay.end()
        self._update_dnd()
        self._update_break()
        self._update_tooltip()
        self.pet.update()
        # keep both settings surfaces showing the same values
        self.popover.load_options(c)
        if self.settings_window is not None:
            self.settings_window.load(c)
        self._save_debounce.start()

    def show_settings(self) -> None:
        from .history import history_path
        from .settings_window import SettingsWindow

        if self.settings_window is None:
            self.settings_window = SettingsWindow(
                str(config_dir()), str(history_path()), self.app.platformName(), self._started_with
            )
            self.settings_window.options_changed.connect(self.set_options)
            self.settings_window.restart_requested.connect(self.restart)
            self.settings_window.test_sound_requested.connect(self._test_sound)
        self.settings_window.load(self.cfg)
        self.settings_window.show()
        self.settings_window.raise_()
        self.settings_window.activateWindow()

    def _test_sound(self) -> None:
        from .notify import play_sound

        play_sound(self.cfg["sound_file"])

    def restart(self) -> None:
        """Start a fresh instance (it waits for this one to exit) and quit."""
        from PySide6.QtCore import QProcess, QProcessEnvironment

        from .wayland import ORIGINAL_QT_QPA_PLATFORM

        # A frozen build (the macOS app) is its own executable.
        args = ["--restarted"] if getattr(sys, "frozen", False) else ["-m", "pomodoro_pet", "--restarted"]
        if self.settings_window is not None and self.settings_window.isVisible():
            args.append("--settings")
        env = QProcessEnvironment.systemEnvironment()
        env.remove("QT_QPA_PLATFORM")  # we may have set it ourselves; let the new backend setting decide
        if ORIGINAL_QT_QPA_PLATFORM:
            env.insert("QT_QPA_PLATFORM", ORIGINAL_QT_QPA_PLATFORM)
        proc = QProcess()
        proc.setProgram(sys.executable)
        proc.setArguments(args)
        proc.setProcessEnvironment(env)
        if not proc.startDetached():
            log.error("Couldn't restart Pomodoro Pet")
            return
        self.quit()

    # ---- update notices --------------------------------------------------
    def _update_checks(self) -> None:
        from .updates import UpdateChecker

        if self.cfg["check_for_updates"] and self.updates is None:
            self.updates = UpdateChecker()
            self.updates.update_available.connect(self._on_update_available)
            self.updates.start()
        elif not self.cfg["check_for_updates"] and self.updates is not None:
            self.updates.stop()
            self.updates = None

    def _on_update_available(self, version: str, page: str) -> None:
        from .notify import notify_with_actions

        self.say(_("Psst! A new version of me is out ({version}) 🎉").format(version=version))
        if self.cfg["notifications"]:
            notify_with_actions(
                _("Pomodoro Pet {version} is available").format(version=version),
                _("You have {current}. Update the way you installed it (your package manager, "
                  "a new download, or git pull).").format(current=__version__),
                [("open", _("See what's new"))],
                lambda _choice: self._open_url(page),
            )

    def _open_url(self, url: str) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl(url))

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
            self._hook_transitions()
        self._update_tooltip()

    # ---- hooks (your scripts in ~/.config/pomodoro-pet/hooks.d) -----------
    def hook(self, event: str, **details) -> None:
        from .hooks import run_hooks

        if not self.cfg["hooks_enabled"]:
            return
        t = self.timer
        info = {
            "phase": t.phase.value, "round": t.round, "rounds": t.rounds,
            "remaining": t.remaining_seconds(), "intention": self.intention, "pet_name": self.cfg["pet_name"],
        }
        info.update(details)
        run_hooks(event, info)

    def _hook_transitions(self) -> None:
        """phase_start / paused / resumed from timer state changes."""
        t = self.timer
        key = (t.round, t.phase)
        if t.state is State.RUNNING:
            if key != self._hook_key:
                self._hook_key = key
                if not t.in_overtime:  # overtime has its own event
                    self.hook("phase_start")
            elif self._hook_prev_state is State.PAUSED:
                self.hook("resumed")
        elif t.state is State.PAUSED and self._hook_prev_state is State.RUNNING and key == self._hook_key:
            self.hook("paused")
        elif t.state in (State.IDLE, State.COMPLETE):
            self._hook_key = None
        self._hook_prev_state = t.state

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
            return _("Session complete")
        left = format_mmss(t.remaining_seconds())
        if t.state is State.IDLE:
            return _("Idle · {left}").format(left=left)
        text = t.phase.label
        if t.phase is Phase.FOCUS:
            text += _(" · round {round}/{rounds}").format(round=t.round, rounds=t.rounds)
        if t.in_overtime:
            text += _(" · +{time} overtime").format(time=format_mmss(t.display_seconds()))
        else:
            text += _(" · {time} left").format(time=left)
        text += _(" (paused)") if t.state is State.PAUSED else ""
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

    # ---- fullscreen apps -------------------------------------------------
    def _on_fullscreen(self, full: bool) -> None:
        self._fullscreen = full
        self._apply_visibility()

    def _apply_visibility(self) -> None:
        """Step aside while a fullscreen video, game or presentation covers the pet."""
        hide = self._fullscreen and self.cfg["hide_in_fullscreen"]
        in_tray = self.tray is not None and self.tray.icon.isVisible()
        if hide and self.pet.isVisible():
            self.popover.hide()
            self.pet.hide()
            self._hidden_for_fullscreen = True
        elif not hide and self._hidden_for_fullscreen:
            self._hidden_for_fullscreen = False
            if not in_tray:
                self.pet.show()

    # ---- quick controls: scroll & right-click ------------------------------
    def _on_scrolled(self, steps: int) -> None:
        """Scroll on the bubble: +/- a minute on the running phase, or the
        focus length while idle."""
        t = self.timer
        if t.in_overtime:
            return
        if t.state in (State.IDLE, State.COMPLETE):
            self.set_options({"focus_minutes": self.cfg["focus_minutes"] + steps})
        else:
            t.adjust(steps * 60)

    def primary_action(self) -> None:
        if self.timer.in_overtime:
            self.timer.take_break()
        else:
            self.timer.toggle()

    def _show_menu(self, pos) -> None:
        from PySide6.QtGui import QIcon
        from PySide6.QtWidgets import QMenu

        t = self.timer
        menu = QMenu(self.pet)

        def add(text: str, icon: str, slot, enabled: bool = True):
            action = menu.addAction(QIcon.fromTheme(icon), text)
            action.triggered.connect(slot)
            action.setEnabled(enabled)
            return action

        running_or_paused = t.state in (State.RUNNING, State.PAUSED)
        if t.in_overtime:
            add(_("Take break"), "media-skip-forward", t.take_break)
        elif t.state is State.RUNNING:
            add(_("Pause"), "media-playback-pause", t.pause)
        else:
            add(_("Resume") if t.state is State.PAUSED else _("Start"), "media-playback-start", t.start)
        if t.can_extend:
            add(_("Keep going"), "media-seek-forward", self._keep_going)
        add(_("Skip"), "media-skip-forward", t.skip, running_or_paused and not t.in_overtime)
        add(_("Reset"), "edit-undo", t.reset, t.state is not State.IDLE)
        add(_("Add 5 minutes"), "list-add", lambda: t.adjust(300), running_or_paused and not t.in_overtime)
        menu.addSeparator()
        add(_("What are you focusing on…"), "document-edit",
            lambda: self.popover.popup_near(self.pet, self.pet.interaction_rect()))
        add(_("Stats"), "view-statistics", self.show_stats)
        add(_("Settings"), "configure", self.show_settings)
        if self.updates is not None and self.updates.latest:
            version, page = self.updates.latest
            add(_("Update available: {version}").format(version=version), "system-software-update",
                lambda: self._open_url(page))
        menu.addSeparator()
        add(_("Quit"), "application-exit", self.quit)
        menu.exec(pos)

    def _mood_line(self) -> str:
        return _("{name} is {mood} {emoji}").format(
            name=self.cfg["pet_name"], mood=self.mood.label, emoji=self.mood.emoji)

    def _on_petted(self) -> None:
        import random

        from .behaviour import PET_LINES

        now = time.time()
        if now - self._last_pet_logged >= 60:  # one "pet" per minute in the log, however long you rub
            self._last_pet_logged = now
            self.history.append({"type": "pet", "end": now, "seconds": 0})
            was_sulky = self.mood.is_sulky
            self._refresh_mood(react=False)
            if was_sulky and not self.mood.is_sulky:
                self.say(_("Okay… I feel better now 🥺💕"))
                self._last_pet_said = now
                return
        if now - self._last_pet_said < 15:
            return
        self._last_pet_said = now
        t = self.timer
        if t.state is State.RUNNING and t.phase is Phase.FOCUS:
            if self._pet_hush_round != t.round:  # once per round: don't distract you
                self._pet_hush_round = t.round
                self.say(_("Hehe! Okay, back to work 🐾"))
        elif self.mood.is_sulky:
            self.say(_("Hmph… okay, that's nice 🥺"))
        else:
            self.say(random.choice(PET_LINES))

    def _on_phase_ended(self, phase, seconds: float, outcome: str, round_: int) -> None:
        from PySide6.QtCore import QTimer

        if seconds < 1 and outcome != "completed":
            return  # skipping a phase that never started isn't worth logging
        self.hook("phase_end", phase=phase.value, round=round_, outcome=outcome, seconds=round(seconds))
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
            self.say(ngettext("{n} round today! Good human 🐾", "{n} rounds today! Good human 🐾", n).format(n=n))
        elif phase.is_break and outcome == "skipped":
            self.say(_("Hey… breaks keep us sharp 😤"))
        elif phase is Phase.FOCUS and outcome == "overtime":
            if seconds >= 60:
                self.say(_("Great flow! {time} extra logged 🌊").format(time=format_minutes(seconds / 60)))
            else:
                self.say(_("Great flow! 🌊"))
        elif phase is Phase.FOCUS and seconds >= 60:
            self.say(_("Aww, we were on a roll…"))
        if self.mood.level != before and self.mood.is_happy and before not in ("happy", "overjoyed"):
            QTimer.singleShot(5200, lambda: self.say(self._mood_line()))

    def _refresh_mood(self, react: bool = True) -> None:
        records = self.history.records
        self.mood = mood(records)
        self.pet.set_mood(self.mood.level)
        s = summarize(records)
        rounds = ngettext("{n} round", "{n} rounds", s.today_rounds).format(n=s.today_rounds)
        summary = _("Today {rounds} · {time}").format(rounds=rounds, time=format_minutes(s.today_minutes))
        if s.streak:
            summary += _(" · 🔥 {n}-day streak").format(n=s.streak)
        self.popover.set_stats(self._mood_line(), summary)
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
        lines.append(f"{self._mood_line()} — {self.mood.reason}")
        self.pet.setToolTip("\n".join(lines))

    # ---- idle detection ----------------------------------------------
    def _on_away(self, idle: float) -> None:
        self.hook("away", seconds=round(idle))
        t = self.timer
        if self.cfg["idle_pause"] and t.in_overtime and t.state is State.RUNNING:
            # You wandered off mid-flow: that's your break starting. The idle bit isn't overtime.
            t.take_break(trim=min(idle, t.overtime()))
            log.info("You stepped away during overtime: started your break")
            return
        if not self.cfg["idle_pause"] or t.state is not State.RUNNING or t.phase is not Phase.FOCUS:
            return
        rewind = min(idle, t.elapsed())
        t.pause(rewind=rewind)
        self._auto_paused = rewind
        log.info("You stepped away: paused focus (gave back %.0f s of idle time)", rewind)

    def _on_back(self, away: float) -> None:
        from .notify import notify_with_actions

        self.hook("back", seconds=round(away))
        if self._auto_paused is None or self.timer.state is not State.PAUSED:
            return
        mins = max(1, round(away / 60))
        if self.cfg["idle_auto_resume"]:
            self.timer.start()
            self.say(_("Welcome back! Picking up where we left off 🐾"))
            return
        self.say(_("Welcome back! I paused while you were away ({n} min) 🐾").format(n=mins))
        pause_token = self._auto_paused

        def on_action(choice: str) -> None:
            if self._auto_paused is not pause_token or self.timer.state is not State.PAUSED:
                return  # already resumed some other way
            if choice == "credit":
                self.timer.credit(away)
            self.timer.start()

        notify_with_actions(
            _("Welcome back!"),
            _("{name} paused your focus round while you were away for {n} min.").format(
                name=self.cfg["pet_name"], n=mins),
            [("resume", _("Resume")), ("credit", _("I was working, count it"))],
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
            notify(_("It's break time!"),
                   _("{name} really wants you to step away from the computer.").format(name=self.cfg["pet_name"]))

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
        elif command == "settings":
            self.show_settings()
        elif command == "show":
            if self.tray is not None:
                self.tray.hide()
            self.pet.show()
            self.pet.raise_()

    # ---- phase events --------------------------------------------------
    def _on_phase_finished(self, finished, nxt) -> None:
        from .notify import notify, notify_with_actions, play_sound

        if self.timer.leaving_overtime:
            return  # you clicked "Take break" yourself: no fanfare needed
        if finished is Phase.FOCUS:
            self.pet.celebrate(4)
            # Lift Do Not Disturb first, or our own "break time" notice gets swallowed.
            self.dnd.set_active(False)
        away = self.timer.away_seconds
        if away and nxt is not None:
            mins = max(1, round(away / 60))
            if finished is Phase.FOCUS:
                summary = _("Welcome back! Focus round ended {n} min ago").format(n=mins)
            else:
                summary = _("Welcome back! Break ended {n} min ago").format(n=mins)
            body = _("Paused at the start of the {phase}. Click the pup or press your shortcut to continue.").format(
                phase=nxt.label.lower())
        elif nxt is None:
            summary, body = _("Pomodoro session complete! 🎉"), _("All rounds done. Good dog, good human.")
        elif nxt is Phase.FOCUS:
            summary = _("Break's over")
            body = _("Time to focus — round {round}/{rounds}.").format(
                round=self.timer.round + 1, rounds=self.timer.rounds)
        elif nxt is Phase.LONG_BREAK:
            summary = _("Focus round done!")
            body = _("Enjoy a {n}-minute long break.").format(n=self.timer.long_break_minutes)
        else:
            summary = _("Focus round done!")
            body = _("Take a {n}-minute break.").format(n=self.timer.short_break_minutes)
        if finished is Phase.FOCUS and self.intention and not away:
            body = f"“{self.intention}” — {body}"
        if nxt is None:
            s = summarize(self.history.records)
            body += " " + ngettext("Today: {n} round, {time}.", "Today: {n} rounds, {time}.", s.today_rounds).format(
                n=s.today_rounds, time=format_minutes(s.today_minutes))
        if not self.timer.auto_advance and nxt is not None and not away:
            body += " " + _("Click the pup to start.")
        if self.cfg["notifications"]:
            if finished is Phase.FOCUS and nxt is not None and nxt.is_break and not away:
                body += " " + _("Or keep going if you're in the flow.")
                notify_with_actions(summary, body, [("keep", _("Keep going"))], self._keep_going)
            else:
                notify(summary, body)
        if self.cfg["sound"]:
            play_sound(self.cfg["sound_file"])

    # ---- flow mode & one-minute warning ---------------------------------
    def _keep_going(self, _choice: str = "keep") -> None:
        if self.timer.extend_focus():
            self.say(_("Okay, keep going! I'll count your overtime 🌊"))
            self.hook("overtime_start")

    def _on_overtime_started(self) -> None:
        from .notify import notify_with_actions, play_sound

        self.pet.celebrate(4)
        self._save_session()
        self.hook("overtime_start")
        if self.cfg["notifications"]:
            mins = self.timer.duration(self.timer.next_phase() or Phase.SHORT_BREAK) // 60
            body = _("You're in the flow, so I'm counting your overtime. "
                     "Take your {n}-minute break when you're ready.").format(n=mins)
            if self.intention:
                body = f"“{self.intention}” — {body}"
            notify_with_actions(
                _("Focus round done! 🌊"), body, [("break", _("Take break now"))],
                lambda _choice: self.timer.take_break(),
            )
        if self.cfg["sound"]:
            play_sound(self.cfg["sound_file"])

    def _on_warning(self, phase, _seconds_left: int) -> None:
        from .assets import asset
        from .notify import play_sound

        if self.activity.is_away:
            return  # nobody to warn
        if phase is Phase.FOCUS:
            self.say(_("One minute left. Time to wrap up your thought 🐾"))
        else:
            self.say(_("Break ends in a minute. Ready to dive back in? 🐾"))
        sound = asset("sounds", "warn.wav")
        if self.cfg["sound"] and sound is not None:
            play_sound(str(sound))

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
        from .notify import cancel_pending

        cancel_pending()
        self.activity.stop()
        if self.updates is not None:
            self.updates.stop()
        self.overlay.end()
        self.dnd.set_active(False)
        self.service.unregister()
        if self.kwin is not None:
            self.kwin.stop()


def lock_owner_is_pet(lock) -> bool:
    """Is the process holding the single-instance lock really Pomodoro Pet?"""
    info = lock.getLockInfo()
    try:
        pid = int(info[0])
    except (TypeError, ValueError, IndexError):
        return True  # can't tell: assume it is
    if os.path.isdir("/proc"):
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as fh:
                args = fh.read().decode(errors="replace").split("\0")
        except OSError:
            return False  # gone
        # `python -m pomodoro_pet ...` or the installed `pomodoro-pet` launcher script
        module = any(a == "-m" and b == "pomodoro_pet" for a, b in zip(args, args[1:], strict=False))
        script = any(os.path.basename(arg) == APP_ID for arg in args[:2])
        return module or script
    # macOS: no /proc, ask ps (the command line comes back as one string)
    import re
    import subprocess

    try:
        out = subprocess.run(
            ["ps", "-ww", "-o", "command=", "-p", str(pid)], capture_output=True, text=True, timeout=3
        )
    except (OSError, subprocess.SubprocessError):
        return True  # can't tell
    command = out.stdout.strip()
    if not command:
        return False  # gone
    # `python -m pomodoro_pet`, the launcher script or the app's .../Contents/MacOS/pomodoro-pet
    return bool(re.search(r"(^|\s)-m pomodoro_pet(\s|$)", command) or re.search(r"(^|/)pomodoro-pet(\s|$)", command))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog=APP_ID, description=_("A Pomodoro desktop pet (Bichon x Biewer pup)."))
    remote = parser.add_argument_group("control a running pet (or start one)")
    for cmd in COMMANDS:
        helps = {
            "toggle": _("start or pause the timer"), "start": _("start/resume the timer"),
            "pause": _("pause the timer"), "skip": _("end the current phase now"),
            "reset": _("reset the session"), "show": _("show the pet"),
            "settings": _("open the settings window"), "status": _("print the current phase and time left"),
        }
        remote.add_argument(f"--{cmd}", dest="command", action="store_const", const=cmd, help=helps[cmd])
    remote.add_argument("--intention", metavar="TEXT", help=_("set what you're focusing on (combine with --start)"))
    parser.add_argument("--restarted", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--stats", action="store_true", help=_("print focus stats and the pup's mood, then exit"))
    parser.add_argument("--backend", choices=("auto", "wayland", "xcb"), help=_("override the config's backend"))
    parser.add_argument("--reset-position", action="store_true", help=_("forget the saved window position"))
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(name)s: %(message)s")

    if args.stats:
        cfg = Config().load()
        print(stats_text(History().load().records, cfg["pet_name"]))
        return 0

    # One pet at a time. If one is already running, this invocation is a remote
    # control: pass the command (or "show") to it (D-Bus, or a local socket on macOS) and exit.
    from PySide6.QtCore import QLockFile

    lock = QLockFile(os.path.join(runtime_dir(), f"{APP_ID}.lock"))
    lock.setStaleLockTime(0)
    # After "Restart now" the old instance may still be shutting down: wait for it.
    if not lock.tryLock(10_000 if args.restarted else 100) and not lock_owner_is_pet(lock):
        # Qt only checks that *a* process with the recorded PID and name
        # ("python3.x") exists; after a crash that PID may now belong to
        # some other Python program.
        log.info("Removing a stale lock left by a pet that's no longer running")
        lock.removeStaleLockFile()
    if not lock.isLocked() and not lock.tryLock(100):
        from PySide6.QtCore import QCoreApplication

        _core = QCoreApplication(sys.argv[:1])  # noqa: F841 (needed for D-Bus / QLocalSocket)
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
        print(_("Pomodoro Pet is not running"))
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
    from .i18n import install_qt_translations

    install_qt_translations(app)
    app.setApplicationVersion(__version__)
    app.setQuitOnLastWindowClosed(False)
    icon = QIcon.fromTheme(APP_ID)
    if icon.isNull() and asset("icons", "pomodoro-pet.png"):
        icon = QIcon(str(asset("icons", "pomodoro-pet.png")))
    app.setWindowIcon(icon)
    log.info("Qt platform: %s", app.platformName())

    controller = Controller(app, cfg, autostart=args.command in ("start", "toggle"), intention=args.intention)
    if args.command == "settings":
        controller.show_settings()

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
