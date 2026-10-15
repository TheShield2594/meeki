"""Stats window: today / streak / week / all-time tiles, a 7-day focus chart
and the week's top focus intentions."""

from __future__ import annotations

from PySide6.QtCore import QDate, QLocale, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QKeySequence, QPainter, QPainterPath, QPalette, QShortcut
from PySide6.QtWidgets import QFrame, QGridLayout, QLabel, QToolTip, QVBoxLayout, QWidget

from .history import Mood, Record, format_minutes, summarize, top_intentions, week_series
from .i18n import _, ngettext


def _day_name(day, short: bool = False) -> str:
    """Weekday name in the desktop's language."""
    return QLocale().dayName(day.isoweekday(), QLocale.ShortFormat if short else QLocale.LongFormat)


def _day_long(day) -> str:
    return QLocale().toString(QDate(day.year, day.month, day.day), "dddd d MMMM")


# Focus red; validated (dataviz palette check) against light and dark surfaces.
BAR_COLOR = QColor("#e2574c")


class WeekChart(QWidget):
    """One series (minutes focused per day), so: one hue, no legend box (the
    heading names it), thin columns with a rounded data end on a single
    baseline, labels only on today and the best day, a tooltip per column."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.series: list[tuple] = []
        self.setMinimumHeight(150)
        self.setMouseTracking(True)
        self._columns: list[QRectF] = []

    def set_series(self, series) -> None:
        self.series = list(series)
        desc = "; ".join(f"{_day_name(d)}: {format_minutes(m)}" for d, m in self.series)
        self.setAccessibleName(_("Minutes focused per day, last 7 days"))
        self.setAccessibleDescription(desc)
        self.update()

    def _ink(self, muted: bool = False) -> QColor:
        role = QPalette.PlaceholderText if muted else QPalette.WindowText
        return self.palette().color(role)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        small = QFont(self.font())
        small.setPointSizeF(max(7.0, small.pointSizeF() * 0.85))
        p.setFont(small)
        fm = p.fontMetrics()

        top_pad, label_h = fm.height() + 6, fm.height() + 6
        plot = QRectF(8, top_pad, self.width() - 16, self.height() - top_pad - label_h)
        n = max(1, len(self.series))
        slot = plot.width() / n
        bar_w = min(24.0, slot * 0.55)
        peak = max((m for _, m in self.series), default=0.0)
        best_i = max(range(n), key=lambda i: self.series[i][1]) if self.series and peak > 0 else -1

        # baseline: hairline, recessive
        base_color = self._ink(muted=True)
        base_color.setAlphaF(0.45)
        p.setPen(base_color)
        p.drawLine(QPointF(plot.left(), plot.bottom() + 0.5), QPointF(plot.right(), plot.bottom() + 0.5))

        self._columns = []
        for i, (day, minutes) in enumerate(self.series):
            cx = plot.left() + slot * (i + 0.5)
            h = 0.0 if peak <= 0 else plot.height() * minutes / peak
            col = QRectF(cx - bar_w / 2, plot.bottom() - h, bar_w, h)
            self._columns.append(QRectF(cx - slot / 2, plot.top(), slot, plot.height()))  # hover target
            if h > 0.5:
                radius = min(4.0, h / 2, bar_w / 2)
                path = QPainterPath()
                path.addRoundedRect(col, radius, radius)
                square_base = QPainterPath()
                square_base.addRect(QRectF(col.left(), col.bottom() - min(h, radius), col.width(), min(h, radius)))
                p.setPen(Qt.NoPen)
                p.setBrush(BAR_COLOR)
                p.drawPath(path.united(square_base))

            is_today = i == n - 1
            p.setPen(self._ink(muted=not is_today))
            day_label = _("Today") if is_today else _day_name(day, short=True)
            label_rect = QRectF(cx - slot / 2, plot.bottom() + 4, slot, label_h)
            p.drawText(label_rect, Qt.AlignHCenter | Qt.AlignTop, day_label)
            if (is_today or i == best_i) and minutes > 0:  # selective direct labels
                p.setPen(self._ink())
                p.drawText(
                    QRectF(cx - slot / 2, col.top() - fm.height() - 2, slot, fm.height()),
                    Qt.AlignHCenter | Qt.AlignBottom, format_minutes(minutes),
                )
        p.end()

    def mouseMoveEvent(self, event) -> None:
        pos = event.position()
        for (day, minutes), rect in zip(self.series, self._columns, strict=False):
            if rect.contains(pos):
                QToolTip.showText(
                    event.globalPosition().toPoint(),
                    _day_long(day) + "\n" + _("{time} focused").format(time=format_minutes(minutes)),
                    self,
                )
                return
        QToolTip.hideText()


class _Tile(QFrame):
    def __init__(self, caption: str):
        super().__init__()
        self.setObjectName("tile")
        self.value = QLabel()
        self.value.setObjectName("tileValue")
        self.detail = QLabel()
        self.detail.setObjectName("tileDetail")
        cap = QLabel(caption)
        cap.setObjectName("tileCaption")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(1)
        layout.addWidget(cap)
        layout.addWidget(self.value)
        layout.addWidget(self.detail)

    def set(self, value: str, detail: str) -> None:
        self.value.setText(value)
        self.detail.setText(detail)


STYLE = """
#tile { border: 1px solid palette(midlight); border-radius: 8px; }
#tileCaption, #tileDetail, #muted { color: palette(placeholder-text); }
#tileValue { font-size: 18pt; font-weight: 600; }
#heading { font-weight: 600; }
#mood { font-size: 12pt; font-weight: 600; }
"""


class StatsWindow(QWidget):
    def __init__(self, pet_name: str):
        super().__init__(None, Qt.Window)
        self.pet_name = pet_name
        self.setWindowTitle(_("Pomodoro Pet — Stats"))
        self.setStyleSheet(STYLE)
        self.setMinimumWidth(440)

        self.mood = QLabel()
        self.mood.setObjectName("mood")
        self.mood_reason = QLabel()
        self.mood_reason.setObjectName("muted")

        self.today, self.streak = _Tile(_("Today")), _Tile(_("Streak"))
        self.week, self.total = _Tile(_("This week")), _Tile(_("All time"))
        tiles = QGridLayout()
        tiles.setSpacing(8)
        for i, tile in enumerate((self.today, self.streak, self.week, self.total)):
            tiles.addWidget(tile, 0, i)

        chart_heading = QLabel(_("Focus time, last 7 days"))
        chart_heading.setObjectName("heading")
        self.chart = WeekChart()

        top_heading = QLabel(_("Top focus this week"))
        top_heading.setObjectName("heading")
        self.top = QGridLayout()
        self.top.setHorizontalSpacing(12)
        self.top.setVerticalSpacing(2)
        self.top.setColumnStretch(1, 1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 16)
        layout.setSpacing(10)
        layout.addWidget(self.mood)
        layout.addWidget(self.mood_reason)
        layout.addLayout(tiles)
        layout.addSpacing(4)
        layout.addWidget(chart_heading)
        layout.addWidget(self.chart)
        layout.addWidget(top_heading)
        layout.addLayout(self.top)
        QShortcut(QKeySequence(Qt.Key_Escape), self, activated=self.close)

    def refresh(self, records: list[Record], mood: Mood, now: float | None = None) -> None:
        s = summarize(records, now)
        self.mood.setText(_("{name} is {mood} {emoji}").format(name=self.pet_name, mood=mood.label, emoji=mood.emoji))
        self.mood_reason.setText(mood.reason.capitalize())
        self.today.set(
            str(s.today_rounds),
            ngettext("round · {time}", "rounds · {time}", s.today_rounds).format(time=format_minutes(s.today_minutes)),
        )
        self.streak.set(
            ngettext("{n} day", "{n} days", s.streak).format(n=s.streak),
            _("best {n}").format(n=s.best_streak),
        )
        self.week.set(format_minutes(s.week_minutes), _("focused"))
        self.total.set(
            str(s.total_rounds),
            ngettext("round · {time}", "rounds · {time}", s.total_rounds).format(time=format_minutes(s.total_minutes)),
        )
        self.chart.set_series(week_series(records, now))
        while self.top.count():
            item = self.top.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        top = top_intentions(records, now)
        if not top:
            hint = QLabel(_("Type what you're working on in the popover and it shows up here."))
            hint.setObjectName("muted")
            hint.setWordWrap(True)
            self.top.addWidget(hint, 0, 0, 1, 2)
        for row, (text, minutes) in enumerate(top):
            amount = QLabel(format_minutes(minutes))
            amount.setObjectName("muted")
            amount.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            name = QLabel(text)
            name.setTextFormat(Qt.PlainText)
            self.top.addWidget(amount, row, 0)
            self.top.addWidget(name, row, 1)
