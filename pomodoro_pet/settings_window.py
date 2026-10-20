"""Settings window. Every change applies straight away (it's emitted as
options_changed({key: value}) and handled by Controller.set_options); only the
display backend needs a restart, which the Advanced tab offers to do."""

from __future__ import annotations

import os
import sys

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from .i18n import _
from .popover import make_spin

STYLE = """
QLabel#hint { color: palette(placeholder-text); }
QLabel#restart { font-weight: 600; }
"""

BREAK_MODES = [
    ("nudge", _("Nudge me"), _("The pup wiggles and reminds you if you keep using the computer during a break.")),
    ("overlay", _("Dim the screen"), _("A full-screen break reminder on every monitor, with snooze and skip.")),
    ("off", _("Off"), _("Breaks are up to you.")),
]
BACKENDS = [
    ("auto", _("Automatic (recommended)")),
    ("wayland", _("Native Wayland")),
    ("xcb", _("XWayland / X11")),
]
RESTART_KEYS = ("backend", "kwin_integration")


def _hint(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("hint")
    label.setWordWrap(True)
    return label


def _page(*rows) -> QWidget:
    page = QWidget()
    lay = QVBoxLayout(page)
    lay.setContentsMargins(16, 14, 16, 14)
    lay.setSpacing(10)
    for row in rows:
        if isinstance(row, QWidget):
            lay.addWidget(row)
        else:
            lay.addLayout(row)
    lay.addStretch(1)
    return page


class _PathPicker(QWidget):
    """Read-only path + Browse + Default (+ optional extra button)."""

    changed = Signal(object)  # str or None

    def __init__(self, placeholder: str, pick, extra: QPushButton | None = None):
        super().__init__()
        self._pick = pick
        self.edit = QLineEdit()
        self.edit.setReadOnly(True)
        self.edit.setPlaceholderText(placeholder)
        browse = QPushButton(_("Browse…"))
        clear = QPushButton(_("Default"))
        browse.clicked.connect(self._browse)
        clear.clicked.connect(lambda: self._set(None))
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.edit, 1)
        lay.addWidget(browse)
        if extra is not None:
            lay.addWidget(extra)
        lay.addWidget(clear)

    def value(self) -> str | None:
        return self.edit.text() or None

    def load(self, value: str | None) -> None:
        self.edit.setText(value or "")

    def _browse(self) -> None:
        path = self._pick(self)
        if path:
            self._set(path)

    def _set(self, path: str | None) -> None:
        if path != self.value():
            self.load(path)
            self.changed.emit(path)


class SettingsWindow(QWidget):
    options_changed = Signal(dict)
    restart_requested = Signal()
    test_sound_requested = Signal()

    def __init__(self, config_dir: str, data_file: str, platform_name: str, started_with: dict):
        super().__init__(None, Qt.Window)
        self.setWindowTitle(_("Pomodoro Pet — Settings"))
        self.setStyleSheet(STYLE)
        self.setMinimumWidth(500)
        self._started_with = started_with  # backend & kwin_integration this process launched with
        self._loading = False
        self.w: dict[str, QWidget] = {}

        tabs = QTabWidget()
        tabs.addTab(self._timer_page(), _("&Timer"))
        tabs.addTab(self._focus_page(), _("&Focus && breaks"))
        tabs.addTab(self._pet_page(), _("&Pet"))
        tabs.addTab(self._sound_page(), _("&Notifications && sound"))
        tabs.addTab(self._advanced_page(config_dir, data_file, platform_name), _("&Advanced"))
        self.tabs = tabs

        close = QPushButton(_("&Close"))
        close.clicked.connect(self.close)
        QShortcut(QKeySequence(Qt.Key_Escape), self, activated=self.close)
        bottom = QHBoxLayout()
        bottom.addWidget(_hint(_("Changes apply right away.")), 1)
        bottom.addWidget(close)
        layout = QVBoxLayout(self)
        layout.addWidget(tabs)
        layout.addLayout(bottom)

    # ---- widget helpers ------------------------------------------------
    def _emit(self, key: str, value) -> None:
        if not self._loading:
            self.options_changed.emit({key: value})
            if key in RESTART_KEYS:
                self._update_restart_note()

    def _check(self, key: str, text: str, tooltip: str = "") -> QCheckBox:
        box = QCheckBox(text)
        box.setToolTip(tooltip)
        box.toggled.connect(lambda on: self._emit(key, on))
        self.w[key] = box
        return box

    def _spin(self, key: str, lo: int, hi: int, suffix: str = ""):
        spin = make_spin(lo, hi, suffix)
        spin.valueChanged.connect(lambda v: self._emit(key, v))
        self.w[key] = spin
        return spin

    def _combo(self, key: str, items) -> QComboBox:
        combo = QComboBox()
        for data, label, *_rest in items:
            combo.addItem(label, data)
        combo.currentIndexChanged.connect(lambda _i: self._emit(key, combo.currentData()))
        self.w[key] = combo
        return combo

    # ---- pages ---------------------------------------------------------
    def _timer_page(self) -> QWidget:
        form = QFormLayout()
        form.addRow(_("Focus"), self._spin("focus_minutes", 1, 240, _(" min")))
        form.addRow(_("Short break"), self._spin("short_break_minutes", 1, 120, _(" min")))
        form.addRow(_("Long break"), self._spin("long_break_minutes", 1, 240, _(" min")))
        form.addRow(_("Rounds before the long break"), self._spin("rounds", 1, 12))
        focus_end = self._combo("flow_mode", [(False, _("Start my break")), (True, _("Keep going (flow mode)"))])
        form.addRow(_("When a focus round ends"), focus_end)
        return _page(
            form,
            _hint(_("Flow mode: a finished round counts as done and the timer counts your overtime up "
                  "until you click Take break. Without it, the round-done notification still has a "
                  "Keep going button.")),
            self._check("auto_advance", _("Start the next phase automatically"),
                        _("Off: the timer waits for you at the start of each phase")),
            self._check("minute_warning", _("One-minute warning"),
                        _("A soft tone, a word from the pup and a pulsing bubble a minute before the end")),
        )

    def _focus_page(self) -> QWidget:
        idle = self._check("idle_pause", _("Pause focus rounds when I step away"))
        minutes = self._spin("idle_minutes", 1, 60, _(" min"))
        resume = self._check("idle_auto_resume", _("Resume by myself when you're back (don't ask)"))
        idle.toggled.connect(minutes.setEnabled)
        idle.toggled.connect(resume.setEnabled)
        idle_row = QHBoxLayout()
        idle_row.addSpacing(24)
        idle_row.addWidget(QLabel(_("Away after no keyboard/mouse input for")))
        idle_row.addWidget(minutes)
        idle_row.addStretch(1)
        resume_row = QHBoxLayout()
        resume_row.addSpacing(24)
        resume_row.addWidget(resume)

        breaks = self._combo("break_enforcement", BREAK_MODES)
        self._break_hint = _hint("")
        breaks.currentIndexChanged.connect(self._update_break_hint)
        break_row = QFormLayout()
        break_row.addRow(_("If I keep working during a break"), breaks)
        return _page(
            self._check("dnd_during_focus", _("Do Not Disturb while focusing"),
                        _("Plasma holds back other notifications during focus rounds")),
            idle, idle_row, resume_row,
            _hint(_("A locked screen counts as away straight away. Idle minutes never count as focus.")),
            break_row, self._break_hint,
        )

    def _pet_page(self) -> QWidget:
        name = QLineEdit()
        name.setMaxLength(40)
        name.editingFinished.connect(lambda: self._emit("pet_name", name.text().strip() or "Biscuit"))
        self.w["pet_name"] = name
        size = make_spin(50, 300, " %")
        size.setSingleStep(10)
        size.valueChanged.connect(lambda v: self._emit("scale", round(v / 100, 2)))
        self.w["scale"] = size
        sprites = _PathPicker(
            _("Bundled Bichon × Biewer pup"),
            lambda parent: QFileDialog.getExistingDirectory(parent, _("Folder with your sprites")),
        )
        sprites.changed.connect(lambda path: self._emit("sprite_dir", path))
        self.w["sprite_dir"] = sprites
        form = QFormLayout()
        form.addRow(_("Name"), name)
        form.addRow(_("Size"), size)
        form.addRow(_("Custom sprites"), sprites)
        return _page(
            form,
            self._check("hide_in_fullscreen", _("Hide while a fullscreen app covers the pet"),
                        _("Videos, games and presentations get the screen to themselves (KDE Plasma)")),
            self._check("idle_animations", _("Idle animations and naps"),
                        _("When the timer isn't running the pup looks around, yawns, scratches and stretches, "
                        "and naps after 5 quiet minutes")),
            _hint(_("Pet the pup by rubbing the pointer back and forth over it.")),
            _hint(_("A sprite folder has one subfolder of PNG frames (or one sprite-sheet PNG) per state: "
                  "idle, focusing, break, celebrating, and optionally sulky, petted, look, yawn, "
                  "scratch and stretch. Missing states use the bundled pup.")),
        )

    def _sound_page(self) -> QWidget:
        test = QPushButton(_("Test"))
        test.clicked.connect(self.test_sound_requested)
        sound = _PathPicker(
            _("Bundled chime"),
            lambda parent: QFileDialog.getOpenFileName(
                parent, _("Sound for the end of a phase"), os.path.expanduser("~"),
                _("Sounds") + " (*.wav *.ogg *.oga *.flac)",
            )[0],
            extra=test,
        )
        sound.changed.connect(lambda path: self._emit("sound_file", path))
        self.w["sound_file"] = sound
        form = QFormLayout()
        form.addRow(_("Phase-end sound"), sound)
        return _page(
            self._check("notifications", _("Show a notification when a phase ends")),
            self._check("sound", _("Play sounds (phase end and one-minute warning)")),
            form,
        )

    def _advanced_page(self, config_dir: str, data_file: str, platform_name: str) -> QWidget:
        backend = self._combo("backend", [(k, label) for k, label in BACKENDS])
        form = QFormLayout()
        form.addRow(_("Display backend"), backend)
        kwin = self._check("kwin_integration", _("Use KWin to keep the pet on top and remember its position (Wayland)"))
        self._restart_label = QLabel(_("Restart Pomodoro Pet to apply this."))
        self._restart_label.setObjectName("restart")
        restart = QPushButton(_("Restart now"))
        restart.clicked.connect(self.restart_requested)
        restart_row = QHBoxLayout()
        restart_row.addWidget(self._restart_label, 1)
        restart_row.addWidget(restart)
        self._restart_widgets = (self._restart_label, restart)

        open_config = QPushButton(_("Open settings folder"))
        open_config.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(config_dir)))
        open_data = QPushButton(_("Open history folder"))
        open_data.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(data_file)))
        )
        open_hooks = QPushButton(_("Open hooks folder"))
        open_hooks.clicked.connect(self._open_hooks)
        files = QHBoxLayout()
        files.addWidget(open_config)
        files.addWidget(open_data)
        files.addWidget(open_hooks)
        files.addStretch(1)
        display = QWidget()  # Linux only: the display backend and KWin
        display_lay = QVBoxLayout(display)
        display_lay.setContentsMargins(0, 0, 0, 0)
        display_lay.addLayout(form)
        display_lay.addWidget(_hint(_("Running on: {platform}. Automatic uses native Wayland on KDE Plasma and "
                                      "XWayland on other Wayland desktops.").format(platform=platform_name)))
        display_lay.addWidget(kwin)
        display_lay.addLayout(restart_row)
        if not sys.platform.startswith("linux"):
            display.hide()
        return _page(
            display,
            self._check("minimize_to_tray", _("Show a “Minimize to tray” button (experimental)")),
            self._check("hooks_enabled", _("Run my hook scripts on timer events"),
                        _("Executables in the hooks folder run on phase start/end, pause, resume, overtime, "
                        "session complete, away and back. See README.txt there.")),
            self._check("check_for_updates", _("Tell me when a new version is out"),
                        _("Once a day, asks GitHub for the latest release. Nothing else is sent.")),
            _hint(_("You have version {version}.").format(version=__version__)),
            files,
        )

    def _open_hooks(self) -> None:
        from .hooks import ensure_hooks_dir

        QDesktopServices.openUrl(QUrl.fromLocalFile(str(ensure_hooks_dir())))

    # ---- state ---------------------------------------------------------
    def _update_break_hint(self, *_args) -> None:
        mode = self.w["break_enforcement"].currentData()
        self._break_hint.setText(next(desc for key, _label, desc in BREAK_MODES if key == mode))

    def _update_restart_note(self) -> None:
        changed = any(self._value(k) != self._started_with.get(k) for k in RESTART_KEYS)
        for widget in self._restart_widgets:
            widget.setVisible(changed)

    def _value(self, key: str):
        w = self.w[key]
        if isinstance(w, QCheckBox):
            return w.isChecked()
        if isinstance(w, QComboBox):
            return w.currentData()
        return None

    def load(self, cfg) -> None:
        """Show the current settings without emitting changes."""
        self._loading = True
        try:
            for key, w in self.w.items():
                value = cfg[key]
                if isinstance(w, QCheckBox):
                    w.setChecked(bool(value))
                elif isinstance(w, QComboBox):
                    w.setCurrentIndex(max(0, w.findData(value)))
                elif isinstance(w, _PathPicker):
                    w.load(value)
                elif isinstance(w, QLineEdit):
                    w.setText(str(value))
                elif key == "scale":
                    w.setValue(round(float(value) * 100))
                else:
                    w.setValue(int(value))
            self.w["idle_minutes"].setEnabled(bool(cfg["idle_pause"]))
            self.w["idle_auto_resume"].setEnabled(bool(cfg["idle_pause"]))
        finally:
            self._loading = False
        self._update_break_hint()
        self._update_restart_note()
