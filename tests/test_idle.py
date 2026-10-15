from pomodoro_pet.idle import ActivityTracker, BreakCoach, nudge_text


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def tracker(away_after=180, poll=5):
    clock = Clock()
    tr = ActivityTracker(source=None, away_after=away_after, poll=poll, clock=clock)
    events = []
    tr.away.connect(lambda idle: events.append(("away", round(idle))))
    tr.back.connect(lambda secs: events.append(("back", round(secs))))
    activity = []
    tr.activity.connect(lambda dt, active: activity.append((round(dt), active)))
    tr.start()
    tr.stop()  # we drive feed() by hand
    return tr, clock, events, activity


def test_away_after_threshold_and_back_with_duration():
    tr, clock, events, _ = tracker()
    for idle in (0, 60, 120, 179):
        clock.t += 60 if idle else 5
        tr.feed(idle)
    assert events == []
    clock.t += 5
    tr.feed(184)  # idle since 184 s ago
    assert events == [("away", 184)]
    clock.t += 300
    tr.feed(484)  # still away: no repeat
    clock.t += 5
    tr.feed(1)  # input again
    assert events == [("away", 184), ("back", 488)]  # from when input stopped until it resumed (1 s ago)


def test_lock_screen_counts_as_away_immediately():
    tr, clock, events, activity = tracker()
    clock.t += 5
    tr.feed(2, locked=True)
    assert events == [("away", 2)]
    clock.t += 5
    tr.feed(0, locked=True)  # still locked: not back yet
    assert len(events) == 1 and activity[-1] == (5, False)
    clock.t += 5
    tr.feed(0, locked=False)
    assert events[-1][0] == "back"


def test_activity_flags_and_unknown_idle():
    tr, clock, _, activity = tracker()
    clock.t += 5
    tr.feed(1)
    clock.t += 5
    tr.feed(30)
    clock.t += 5
    tr.feed(None)  # no idle info on this desktop: ignored
    assert activity == [(5, True), (5, False)]


def test_break_coach_grace_nudge_repeat_and_reset():
    coach = BreakCoach(grace=20, nudge_after=30, repeat=60)
    nudges = [coach.feed(5, True) for _ in range(4)]  # 20 s of grace
    assert nudges == [None] * 4
    out = [coach.feed(5, True) for _ in range(6)]  # 30 s of continuous use
    assert out[-1] == 1 and out[:-1] == [None] * 5
    out = [coach.feed(5, True) for _ in range(12)]  # 60 more
    assert out[-1] == 2
    assert coach.feed(5, False) is None  # stepped away: run resets
    out = [coach.feed(5, True) for _ in range(6)]
    assert out[-1] == 3
    assert coach.active_seconds == 5 * (4 + 6 + 12 + 6)
    coach.begin()
    assert coach.nudges == 0 and coach.active_seconds == 0


def test_nudge_text_escalates_and_caps():
    assert nudge_text(1) != nudge_text(2) != nudge_text(3)
    assert nudge_text(7) == nudge_text(3)
