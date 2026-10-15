"""Quick-controls popover shown when the pet is clicked: what you're working
on, the four durations, Start/Skip/Reset, the pup's mood, and buttons for the
Stats and Settings windows. Everything else lives in the Settings window."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QStringListModel, Qt, Signal
from PySide6.QtWidgets import (
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

DURATION_KEYS = ("focus_minutes", "short_break_minutes", "long_break_minutes", "rounds")


def make_spin(lo: int, hi: int, suffix: str = "") -> QSpinBox:
    spin = QSpinBox()
    spin.setRange(lo, hi)
    spin.setSuffix(suffix)
    spin.setAlignment(Qt.AlignRight)
    return spin


class Popover(QFrame):
    options_changed = Signal(dict)  # {config key: value}
    intention_changed = Signal(str)
    stats_requested = Signal()
    settings_requested = Signal()
    minimize_requested = Signal()

    def __init__(self, timer: PomodoroTimer, parent: QWidget):
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

        self.spins = {
            "focus_minutes": make_spin(1, 240, " min"),
            "short_break_minutes": make_spin(1, 120, " min"),
            "long_break_minutes": make_spin(1, 240, " min"),
            "rounds": make_spin(1, 12),
        }
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        for label, key in zip(("Focus", "Short break", "Long break", "Rounds"), DURATION_KEYS, strict=True):
            form.addRow(label, self.spins[key])
            self.spins[key].valueChanged.connect(lambda value, k=key: self.options_changed.emit({k: value}))

        self.start_btn = QPushButton("Start")
        self.start_btn.setObjectName("primary")
        self.start_btn.setDefault(True)
        self.reset_btn = QPushButton("Reset")
        self.skip_btn = QPushButton("Skip")
        buttons = QHBoxLayout()
        buttons.addWidget(self.start_btn, 2)
        buttons.addWidget(self.skip_btn, 1)
        buttons.addWidget(self.reset_btn, 1)

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)
        self.mood_label = QLabel()
        self.summary_label = QLabel()
        self.summary_label.setObjectName("muted")
        self.stats_btn = QPushButton("Stats")
        self.stats_btn.setToolTip("Focus history, streaks and charts")
        self.settings_btn = QPushButton("Settings")
        self.settings_btn.setToolTip("Breaks, idle detection, flow mode, sounds, the pup…")
        self.tray_btn = QPushButton("Minimize to tray (experimental)")
        footer = QHBoxLayout()
        footer.addWidget(self.stats_btn)
        footer.addWidget(self.settings_btn)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(8)
        layout.addWidget(self.status)
        layout.addWidget(self.intention)
        layout.addLayout(form)
        layout.addLayout(buttons)
        layout.addWidget(line)
        layout.addWidget(self.mood_label)
        layout.addWidget(self.summary_label)
        layout.addLayout(footer)
        layout.addWidget(self.tray_btn)
        self.tray_btn.hide()

        self.intention.editingFinished.connect(self._commit_intention)
        self.intention.returnPressed.connect(self._start_from_intention)
        for btn, signal in (
            (self.stats_btn, self.stats_requested),
            (self.settings_btn, self.settings_requested),
            (self.tray_btn, self.minimize_requested),
        ):
            btn.clicked.connect(signal)
            btn.clicked.connect(self.hide)
        self.start_btn.clicked.connect(self._commit_intention)
        self.start_btn.clicked.connect(self._primary)
        self.reset_btn.clicked.connect(timer.reset)
        self.skip_btn.clicked.connect(self._secondary)

        timer.state_changed.connect(self.refresh)
        timer.phase_changed.connect(self.refresh)
        timer.overtime_started.connect(self.refresh)

    # ---- loading values (no signals) -----------------------------------
    def load_options(self, cfg) -> None:
        for key, spin in self.spins.items():
            spin.blockSignals(True)
            spin.setValue(cfg[key])
            spin.blockSignals(False)
        self.tray_btn.setVisible(bool(cfg["minimize_to_tray"]))
        self.refresh()

    def set_intention(self, text: str) -> None:
        self._committed_intention = text
        if self.intention.text() != text:
            self.intention.setText(text)

    def set_suggestions(self, items: list[str]) -> None:
        self._suggestions.setStringList(items)

    def set_stats(self, mood_text: str, summary_text: str) -> None:
        self.mood_label.setText(mood_text)
        self.summary_label.setText(summary_text)

    # ---- actions -------------------------------------------------------
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

    def _primary(self) -> None:
        """Start/Pause/Resume, or "Take break" in overtime."""
        if self.timer.in_overtime:
            self.timer.take_break()
        else:
            self.timer.toggle()

    def _secondary(self) -> None:
        """Skip, or Pause/Resume in overtime (where the primary is Take break)."""
        if self.timer.in_overtime:
            self.timer.toggle()
        else:
            self.timer.skip()

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
            if t.in_overtime:
                phase += " · overtime 🌊"
            text = phase + (" (paused)" if t.state is State.PAUSED else "")
        self.status.setText(text)
        labels = {State.RUNNING: "Pause", State.PAUSED: "Resume"}
        if t.in_overtime:
            self.start_btn.setText("Take break")
            self.skip_btn.setText(labels.get(t.state, "Pause"))
            self.skip_btn.setToolTip("")
        else:
            self.start_btn.setText(labels.get(t.state, "Start"))
            self.skip_btn.setText("Skip")
            self.skip_btn.setToolTip("End the current phase now")
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
