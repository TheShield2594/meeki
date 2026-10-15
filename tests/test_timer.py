from pomodoro_pet.timer import Phase, PomodoroTimer, State, format_mmss


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def make(**kw):
    clock = Clock()
    timer = PomodoroTimer(clock=clock, **kw)
    return timer, clock


def run_for(timer, clock, seconds):
    clock.t += seconds
    timer.update()


def test_defaults_and_idle():
    t, _ = make()
    assert (t.focus_minutes, t.short_break_minutes, t.long_break_minutes, t.rounds) == (25, 5, 15, 4)
    assert t.state is State.IDLE and t.phase is Phase.FOCUS
    assert t.remaining_seconds() == 25 * 60


def test_countdown_pause_resume():
    t, c = make()
    ticks = []
    t.tick.connect(ticks.append)
    t.start()
    run_for(t, c, 61)
    assert t.remaining_seconds() == 25 * 60 - 61
    t.pause()
    c.t += 500  # time passes while paused
    assert t.remaining_seconds() == 25 * 60 - 61
    t.start()
    run_for(t, c, 9)
    assert t.remaining_seconds() == 25 * 60 - 70
    assert ticks[-1] == 25 * 60 - 70


def test_full_session_sequence():
    t, c = make(focus_minutes=1, short_break_minutes=1, long_break_minutes=2, rounds=2)
    finished = []
    t.phase_finished.connect(lambda a, b: finished.append((a, b)))
    done = []
    t.session_complete.connect(lambda: done.append(True))
    t.start()
    run_for(t, c, 60)
    assert t.phase is Phase.SHORT_BREAK and t.round == 1 and t.state is State.RUNNING
    run_for(t, c, 60)
    assert t.phase is Phase.FOCUS and t.round == 2
    run_for(t, c, 60)
    assert t.phase is Phase.LONG_BREAK
    run_for(t, c, 120)
    assert t.state is State.COMPLETE and done == [True]
    assert finished == [
        (Phase.FOCUS, Phase.SHORT_BREAK),
        (Phase.SHORT_BREAK, Phase.FOCUS),
        (Phase.FOCUS, Phase.LONG_BREAK),
        (Phase.LONG_BREAK, None),
    ]
    t.start()  # starting again after completion begins a fresh session
    assert t.state is State.RUNNING and t.phase is Phase.FOCUS and t.round == 1


def test_no_auto_advance_pauses_between_phases():
    t, c = make(focus_minutes=1, auto_advance=False)
    t.start()
    run_for(t, c, 60)
    assert t.phase is Phase.SHORT_BREAK and t.state is State.PAUSED
    assert t.remaining_seconds() == 5 * 60


def test_reset_and_configure():
    t, c = make()
    t.start()
    run_for(t, c, 30)
    t.reset()
    assert t.state is State.IDLE and t.remaining_seconds() == 25 * 60
    t.configure(50, 10, 20, 3)
    assert t.remaining_seconds() == 50 * 60 and t.rounds == 3


def test_configure_while_running_keeps_remaining():
    t, c = make()
    t.start()
    run_for(t, c, 60)
    t.configure(10, 5, 15, 4)
    assert t.remaining_seconds() == 24 * 60


def test_skip():
    t, c = make()
    t.start()
    t.skip()
    assert t.phase is Phase.SHORT_BREAK


def test_format():
    assert format_mmss(0) == "00:00"
    assert format_mmss(25 * 60) == "25:00"
    assert format_mmss(61) == "01:01"
    assert format_mmss(240 * 60) == "4:00:00"
