"""Small settings/controls popover shown when the pet is clicked."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QStringListModel, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QCompleter,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .timer import Phase, PomodoroTimer, State
from .wayland import running_on_wayland

STYLE = """
#popover {
    background: palette(window);
    border: 1px solid palette(mid);
    border-radius: 10px;
}
#status { font-weight: 600; }
#muted { color: palette(placeholder-text); }
QPushButton#primary { font-weight: 600; }
"""


class Popover(QFrame):
    settings_changed = Signal(int, int, int, int)  # focus, short, long, rounds
    toggles_changed = Signal(bool, bool, bool)  # notifications, sound, do-not-disturb
    minimize_requested = Signal()
    intention_changed = Signal(str)
    stats_requested = Signal()

    def __init__(self, timer: PomodoroTimer, parent: QWidget, show_minimize: bool = False):
        # Qt.Popup -> xdg_popup on Wayland, so it's positioned relative to the
        # pet even though we can't know global coordinates there.
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        self.setObjectName("popover")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)  # rounded corners
        self.setStyleSheet(STYLE)
        self.timer = timer

        self.status = QLabel()
        self.status.setObjectName("status")

        self.intention = QLineEdit()
        self.intention.setPlaceholderText("What are you focusing on?")
        self.intention.setMaxLength(120)
        self.intention.setClearButtonEnabled(True)
        self.intention.setToolTip("Logged with each round. Press Enter to start focusing.")
        self._suggestions = QStringListModel(self)
        completer = QCompleter(self._suggestions, self)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains)
        self.intention.setCompleter(completer)
        self._committed_intention = ""

        self.focus = self._spin(1, 240, " min")
        self.short = self._spin(1, 120, " min")
        self.long = self._spin(1, 240, " min")
        self.rounds = self._spin(1, 12, "")

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.addRow("Focus", self.focus)
        form.addRow("Short break", self.short)
        form.addRow("Long break", self.long)
        form.addRow("Rounds", self.rounds)

        self.notify_box = QCheckBox("Notify")
        self.sound_box = QCheckBox("Sound")
        checks = QHBoxLayout()
        checks.addWidget(self.notify_box)
        checks.addWidget(self.sound_box)
        checks.addStretch(1)
        self.dnd_box = QCheckBox("Do Not Disturb while focusing")
        self.dnd_box.setToolTip("Silence other notifications during focus rounds")

        self.start_btn = QPushButton("Start")
        self.start_btn.setObjectName("primary")
        self.start_btn.setDefault(True)
        self.reset_btn = QPushButton("Reset")
        self.skip_btn = QPushButton("Skip")
        self.skip_btn.setToolTip("End the current phase now")
        buttons = QHBoxLayout()
        buttons.addWidget(self.start_btn, 2)
        buttons.addWidget(self.skip_btn, 1)
        buttons.addWidget(self.reset_btn, 1)

        self.mood_label = QLabel()
        self.stats_btn = QPushButton("Stats")
        self.stats_btn.setToolTip("Focus history, streaks and charts")
        mood_row = QHBoxLayout()
        mood_row.addWidget(self.mood_label, 1)
        mood_row.addWidget(self.stats_btn)
        self.summary_label = QLabel()
        self.summary_label.setObjectName("muted")
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(8)
        layout.addWidget(self.status)
        layout.addWidget(self.intention)
        layout.addLayout(form)
        layout.addLayout(checks)
        layout.addWidget(self.dnd_box)
        layout.addLayout(buttons)
        layout.addWidget(line)
        layout.addLayout(mood_row)
        layout.addWidget(self.summary_label)

        if show_minimize:
            tray_btn = QPushButton("Minimize to tray (experimental)")
            tray_btn.clicked.connect(self.minimize_requested)
            tray_btn.clicked.connect(self.hide)
            layout.addWidget(tray_btn)

        for spin in (self.focus, self.short, self.long, self.rounds):
            spin.valueChanged.connect(self._emit_settings)
        self.notify_box.toggled.connect(self._emit_toggles)
        self.sound_box.toggled.connect(self._emit_toggles)
        self.dnd_box.toggled.connect(self._emit_toggles)
        self.intention.editingFinished.connect(self._commit_intention)
        self.intention.returnPressed.connect(self._start_from_intention)
        self.stats_btn.clicked.connect(self.stats_requested)
        self.stats_btn.clicked.connect(self.hide)
        self.start_btn.clicked.connect(self._commit_intention)
        self.start_btn.clicked.connect(timer.toggle)
        self.reset_btn.clicked.connect(timer.reset)
        self.skip_btn.clicked.connect(timer.skip)

        timer.state_changed.connect(self.refresh)
        timer.phase_changed.connect(self.refresh)

    @staticmethod
    def _spin(lo: int, hi: int, suffix: str) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(lo, hi)
        spin.setSuffix(suffix)
        spin.setAlignment(Qt.AlignRight)
        return spin

    # ---- intention / stats ---------------------------------------------
    def _commit_intention(self) -> None:
        text = self.intention.text().strip()
        if text != self._committed_intention:
            self._committed_intention = text
            self.intention_changed.emit(text)

    def _start_from_intention(self) -> None:
        self._commit_intention()
        if self.timer.state is not State.RUNNING:
            self.timer.start()
        self.hide()

    def set_intention(self, text: str) -> None:
        self._committed_intention = text
        if self.intention.text() != text:
            self.intention.setText(text)

    def set_suggestions(self, items: list[str]) -> None:
        self._suggestions.setStringList(items)

    def set_stats(self, mood_text: str, summary_text: str) -> None:
        self.mood_label.setText(mood_text)
        self.summary_label.setText(summary_text)

    def load(
        self, focus: int, short: int, long: int, rounds: int, notifications: bool, sound: bool, dnd: bool
    ) -> None:
        widgets = (self.focus, self.short, self.long, self.rounds, self.notify_box, self.sound_box, self.dnd_box)
        for w in widgets:
            w.blockSignals(True)
        self.focus.setValue(focus)
        self.short.setValue(short)
        self.long.setValue(long)
        self.rounds.setValue(rounds)
        self.notify_box.setChecked(notifications)
        self.sound_box.setChecked(sound)
        self.dnd_box.setChecked(dnd)
        for w in widgets:
            w.blockSignals(False)
        self.refresh()

    def _emit_settings(self) -> None:
        self.settings_changed.emit(self.focus.value(), self.short.value(), self.long.value(), self.rounds.value())

    def _emit_toggles(self) -> None:
        self.toggles_changed.emit(self.notify_box.isChecked(), self.sound_box.isChecked(), self.dnd_box.isChecked())

    def refresh(self, *_args) -> None:
        t = self.timer
        if t.state is State.COMPLETE:
            text = "Session complete! 🎉"
        elif t.state is State.IDLE:
            text = "Ready when you are"
        else:
            phase = t.phase.label
            if t.phase is Phase.FOCUS:
                phase += f" · round {t.round}/{t.rounds}"
            text = phase + (" (paused)" if t.state is State.PAUSED else "")
        self.status.setText(text)
        labels = {State.RUNNING: "Pause", State.PAUSED: "Resume"}
        self.start_btn.setText(labels.get(t.state, "Start"))
        self.skip_btn.setEnabled(t.state in (State.RUNNING, State.PAUSED))

    def popup_near(self, anchor: QWidget, local_rect) -> None:
        """Show next to `local_rect` (in anchor coordinates), preferring the right side."""
        self.refresh()
        self.adjustSize()
        target = anchor.mapToGlobal(QPoint(local_rect.right() + 6, local_rect.top()))
        screen = anchor.screen()
        if screen is not None and not running_on_wayland():
            area = screen.availableGeometry()
            # On X11 we know real coordinates and can keep it on screen. On
            # Wayland the compositor flips/slides xdg_popups for us.
            if target.x() + self.width() > area.right():
                target.setX(anchor.mapToGlobal(QPoint(local_rect.left(), 0)).x() - self.width() - 6)
            if target.y() + self.height() > area.bottom():
                target.setY(area.bottom() - self.height())
        self.move(target)
        self.show()
        if self.timer.state is State.RUNNING:
            self.start_btn.setFocus()  # Space pauses
        else:
            self.intention.setFocus()  # type, Enter starts
            self.intention.selectAll()
