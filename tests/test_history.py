import json
from datetime import datetime, timedelta

from pomodoro_pet.history import (
    History,
    best_streak,
    current_streak,
    format_minutes,
    mood,
    recent_intentions,
    stats_text,
    summarize,
    top_intentions,
    week_series,
)

NOW = datetime(2026, 10, 15, 16, 0).timestamp()


def at(days_ago: int, hour: int = 10) -> float:
    return (datetime(2026, 10, 15, hour, 0) - timedelta(days=days_ago)).timestamp()


def focus(days_ago=0, outcome="completed", seconds=1500, intention=None, hour=10):
    rec = {"type": "focus", "outcome": outcome, "end": at(days_ago, hour), "seconds": seconds}
    if intention:
        rec["intention"] = intention
    return rec


def brk(days_ago=0, outcome="completed", kind="short_break"):
    return {"type": kind, "outcome": outcome, "end": at(days_ago), "seconds": 300}


def test_append_and_load_skips_corrupt_lines(tmp_path):
    path = tmp_path / "h" / "history.jsonl"
    h = History(path)
    h.append(focus())
    with open(path, "a") as fh:
        fh.write("{broken\n\n")
        fh.write(json.dumps({"no": "end"}) + "\n")
    h.append(brk())
    loaded = History(path).load().records
    assert [r["type"] for r in loaded] == ["focus", "short_break"]
    assert loaded[0]["v"] == 1


def test_summary_counts_partial_focus_time_but_not_rounds():
    records = [focus(), focus(outcome="skipped", seconds=600), brk(), focus(days_ago=1)]
    s = summarize(records, NOW)
    assert s.today_rounds == 1
    assert round(s.today_minutes) == 35
    assert s.total_rounds == 2
    assert round(s.week_minutes) == 60


def test_streaks():
    records = [focus(0), focus(1), focus(2), focus(5), focus(6), focus(7), focus(8)]
    assert current_streak(records, NOW) == 3
    assert best_streak(records) == 4
    # no round yet today: yesterday's streak is still alive
    assert current_streak([focus(1), focus(2)], NOW) == 2
    # a skipped round doesn't keep a streak going
    assert current_streak([focus(0, outcome="skipped")], NOW) == 0


def test_week_series_and_intentions():
    records = [
        focus(0, intention="Write report"),
        focus(0, intention="Write report", seconds=900),
        focus(3, intention="Email"),
        focus(9, intention="Old stuff"),
    ]
    series = week_series(records, NOW)
    assert len(series) == 7 and series[-1][0] == datetime.fromtimestamp(NOW).date()
    assert round(series[-1][1]) == 40
    assert top_intentions(records, NOW) == [("Write report", 40.0), ("Email", 25.0)]
    assert recent_intentions(records) == ["Old stuff", "Email", "Write report"]


def test_mood_levels():
    assert mood([], NOW).level == "content"
    happy = mood([focus(0), focus(0), focus(0), focus(1), focus(2)], NOW)
    assert happy.is_happy and "3 rounds done today" in happy.reason
    great = mood([focus(0, hour=h) for h in range(8, 14)] + [focus(d) for d in range(1, 8)], NOW)
    assert great.level == "overjoyed"
    sulky = mood([brk(0, "skipped"), brk(0, "skipped"), focus(0, "reset", 600)], NOW)
    assert sulky.is_sulky and sulky.reason.startswith("skipped 2 breaks")
    missed = mood([focus(4)], NOW)
    assert missed.level == "sulky" and "4 days" in missed.reason


def test_stats_text_and_format():
    text = stats_text([focus(0, intention="Write report")], "Biscuit", NOW)
    assert text.startswith("Biscuit is ")
    assert "Today      1 round · 25m focused" in text
    assert "Write report" in text
    assert format_minutes(59.6) == "1h 00m"
    assert format_minutes(135) == "2h 15m"


def test_working_through_a_break_upsets_the_pup():
    worked = dict(brk(0), active_seconds=250)  # 250 of 300 s at the keyboard
    rested = dict(brk(0), active_seconds=30)
    assert mood([focus(0), rested], NOW).score > mood([focus(0), worked], NOW).score
    assert "worked through 1 break today" in mood([focus(0), worked], NOW).reason


def test_pets_help_a_little_but_are_capped():
    pet = {"type": "pet", "end": at(0), "seconds": 0}
    base = mood([], NOW).score
    assert mood([pet], NOW).score == base + 3
    assert mood([pet] * 10, NOW).score == base + 9  # capped at three
    assert "got 1 pet today" in mood([pet], NOW).reason
    assert summarize([pet], NOW).today_minutes == 0  # pets aren't focus time
