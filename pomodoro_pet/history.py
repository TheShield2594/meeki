"""Focus history, stats and the pup's mood.

Every phase that ends (completed, skipped or reset) is appended as one JSON
line to ~/.local/share/pomodoro-pet/history.jsonl:

    {"v": 1, "type": "focus", "outcome": "completed", "end": 1760000000.0,
     "seconds": 1500, "intention": "Write report", "round": 2}

"type" is focus / short_break / long_break; "outcome" is completed / skipped /
reset. Everything below is a pure function of those records and "now", so it's
easy to test and works without the app running (`pomodoro-pet --stats`).
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from .config import APP_ID

log = logging.getLogger(__name__)

Record = dict[str, Any]


def history_path() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return Path(base) / APP_ID / "history.jsonl"


class History:
    def __init__(self, path: Path | None = None):
        self.path = path or history_path()
        self.records: list[Record] = []

    def load(self) -> History:
        self.records = []
        try:
            with open(self.path, encoding="utf-8") as fh:
                for n, line in enumerate(fh, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        log.warning("Skipping corrupt line %d in %s", n, self.path)
                        continue
                    if isinstance(rec, dict) and isinstance(rec.get("end"), (int, float)):
                        self.records.append(rec)
        except FileNotFoundError:
            pass
        except OSError as exc:
            log.warning("Could not read history %s: %s", self.path, exc)
        return self

    def append(self, record: Record) -> None:
        record = {"v": 1, **record}
        self.records.append(record)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as exc:
            log.warning("Could not write history %s: %s", self.path, exc)


# --------------------------------------------------------------------------
# stats
# --------------------------------------------------------------------------
def _day(rec: Record) -> date:
    return datetime.fromtimestamp(rec["end"]).date()


def _today(now: float | None) -> date:
    return datetime.fromtimestamp(time.time() if now is None else now).date()


def _is_focus(rec: Record) -> bool:
    return rec.get("type") == "focus"


def _completed(rec: Record) -> bool:
    return _is_focus(rec) and rec.get("outcome") == "completed"


def focus_seconds_by_day(records: list[Record]) -> dict[date, float]:
    """Focused time per day, including partial (skipped/reset) rounds."""
    totals: dict[date, float] = {}
    for rec in records:
        if _is_focus(rec):
            d = _day(rec)
            totals[d] = totals.get(d, 0.0) + float(rec.get("seconds", 0))
    return totals


def completed_days(records: list[Record]) -> set[date]:
    return {_day(r) for r in records if _completed(r)}


def current_streak(records: list[Record], now: float | None = None) -> int:
    """Consecutive days with at least one completed round, ending today, or
    yesterday if today has none yet (the streak isn't lost until tonight)."""
    days = completed_days(records)
    day = _today(now)
    if day not in days:
        day -= timedelta(days=1)
    streak = 0
    while day in days:
        streak += 1
        day -= timedelta(days=1)
    return streak


def best_streak(records: list[Record]) -> int:
    best = run = 0
    prev = None
    for d in sorted(completed_days(records)):
        run = run + 1 if prev is not None and d - prev == timedelta(days=1) else 1
        best = max(best, run)
        prev = d
    return best


def week_series(records: list[Record], now: float | None = None) -> list[tuple[date, float]]:
    """Minutes focused on each of the last 7 days, oldest first."""
    totals = focus_seconds_by_day(records)
    today = _today(now)
    return [(today - timedelta(days=i), totals.get(today - timedelta(days=i), 0.0) / 60) for i in range(6, -1, -1)]


def top_intentions(records: list[Record], now: float | None = None, days: int = 7, limit: int = 5):
    """[(intention, minutes)] for the last `days` days, biggest first."""
    since = _today(now) - timedelta(days=days - 1)
    minutes: Counter[str] = Counter()
    for rec in records:
        text = (rec.get("intention") or "").strip()
        if _is_focus(rec) and text and _day(rec) >= since:
            minutes[text] += float(rec.get("seconds", 0)) / 60
    return minutes.most_common(limit)


def recent_intentions(records: list[Record], limit: int = 20) -> list[str]:
    seen: list[str] = []
    for rec in reversed(records):
        text = (rec.get("intention") or "").strip()
        if _is_focus(rec) and text and text not in seen:
            seen.append(text)
            if len(seen) >= limit:
                break
    return seen


@dataclass
class Summary:
    today_rounds: int
    today_minutes: float
    streak: int
    best_streak: int
    week_minutes: float
    total_rounds: int
    total_minutes: float


def summarize(records: list[Record], now: float | None = None) -> Summary:
    today = _today(now)
    by_day = focus_seconds_by_day(records)
    return Summary(
        today_rounds=sum(1 for r in records if _completed(r) and _day(r) == today),
        today_minutes=by_day.get(today, 0.0) / 60,
        streak=current_streak(records, now),
        best_streak=best_streak(records),
        week_minutes=sum(m for _, m in week_series(records, now)),
        total_rounds=sum(1 for r in records if _completed(r)),
        total_minutes=sum(by_day.values()) / 60,
    )


def format_minutes(minutes: float) -> str:
    minutes = int(round(minutes))
    if minutes < 60:
        return f"{minutes}m"
    h, m = divmod(minutes, 60)
    return f"{h}h {m:02d}m"


# --------------------------------------------------------------------------
# mood
# --------------------------------------------------------------------------
@dataclass
class Mood:
    score: int  # 0..100
    level: str  # overjoyed / happy / content / sulky / sad
    emoji: str
    reason: str

    @property
    def is_sulky(self) -> bool:
        return self.level in ("sulky", "sad")

    @property
    def is_happy(self) -> bool:
        return self.level in ("happy", "overjoyed")


_LEVELS = [(80, "overjoyed", "🥰"), (60, "happy", "😊"), (40, "content", "🙂"), (20, "sulky", "😤"), (0, "sad", "🥺")]


def mood(records: list[Record], now: float | None = None) -> Mood:
    """The pup likes finished rounds and streaks, dislikes skipped breaks and
    abandoned rounds, and misses you after a couple of days away."""
    today = _today(now)
    todays = [r for r in records if _day(r) == today]
    done = sum(1 for r in todays if _completed(r))
    skipped_breaks = sum(1 for r in todays if r.get("type", "").endswith("break") and r.get("outcome") == "skipped")
    abandoned = sum(
        1 for r in todays if _is_focus(r) and r.get("outcome") in ("skipped", "reset") and r.get("seconds", 0) >= 60
    )
    streak = current_streak(records, now)
    days = completed_days(records)
    away_days = (today - max(days)).days if days else 0

    score = 50 + 8 * min(done, 5) + 2 * min(streak, 10) - 12 * skipped_breaks - 6 * abandoned
    if away_days >= 2:
        score -= 10 * min(away_days - 1, 3)
    score = max(0, min(100, score))

    reasons = []
    if skipped_breaks:
        reasons.append(f"skipped {skipped_breaks} break{'s' if skipped_breaks > 1 else ''} today")
    if away_days >= 2:
        reasons.append(f"hasn't seen you in {away_days} days")
    if abandoned:
        reasons.append(f"{abandoned} unfinished round{'s' if abandoned > 1 else ''} today")
    if done:
        reasons.append(f"{done} round{'s' if done > 1 else ''} done today")
    if streak >= 2:
        reasons.append(f"{streak}-day streak")
    if not reasons:
        reasons.append("ready to work with you")

    level, emoji = next((name, e) for floor, name, e in _LEVELS if score >= floor)
    return Mood(score, level, emoji, ", ".join(reasons[:2]))


def _rounds(n: int) -> str:
    return f"{n} round{'s' if n != 1 else ''}"


def stats_text(records: list[Record], pet_name: str, now: float | None = None) -> str:
    """Plain-text report for `pomodoro-pet --stats`."""
    s = summarize(records, now)
    m = mood(records, now)
    lines = [
        f"{pet_name} is {m.level} {m.emoji} ({m.reason})",
        "",
        f"Today      {_rounds(s.today_rounds)} · {format_minutes(s.today_minutes)} focused",
        f"Streak     {s.streak} day{'s' if s.streak != 1 else ''} (best {s.best_streak})",
        f"This week  {format_minutes(s.week_minutes)}",
        f"All time   {_rounds(s.total_rounds)} · {format_minutes(s.total_minutes)}",
        "",
        "Last 7 days",
    ]
    series = week_series(records, now)
    peak = max((m for _, m in series), default=0) or 1
    for d, minutes in series:
        bar = "█" * round(20 * minutes / peak)
        lines.append(f"  {d.strftime('%a %d')}  {bar:<20} {format_minutes(minutes)}")
    top = top_intentions(records, now)
    if top:
        lines += ["", "Top focus this week"]
        lines += [f"  {format_minutes(minutes):>7}  {text}" for text, minutes in top]
    return "\n".join(lines)
