import random

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from pomodoro_pet.behaviour import IdleLife, RubDetector


# ---- rub detection ------------------------------------------------------------
def rub(det, xs, start=0.0, step=0.05):
    return [det.feed(x, start + i * step) for i, x in enumerate(xs)]


def test_rubbing_back_and_forth_is_petting():
    det = RubDetector(min_travel=8, strokes=3, window=1.6)
    xs = [10, 20, 30, 20, 10, 20, 30, 20, 10]  # right, left, right, left
    assert any(rub(det, xs))


def test_passing_over_or_jittering_is_not():
    det = RubDetector()
    assert not any(rub(det, range(0, 100, 5)))  # one sweep across
    det.reset()
    assert not any(rub(det, [50, 52, 50, 52, 50, 52, 50, 52, 50]))  # tiny jitters
    det.reset()
    # back and forth, but too slowly to be a rub
    assert not any(rub(det, [10, 30, 10, 30, 10, 30, 10], step=1.0))


# ---- idle life ------------------------------------------------------------------
class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def life(**kw):
    clock = Clock()
    return IdleLife(clock=clock, rng=random.Random(4), **kw), clock


ALL = set(IdleLife.ACTIONS)


def test_actions_come_now_and_then_never_twice_in_a_row():
    lf, clock = life(gap=(25, 70))
    assert lf.tick(True, ALL) is None  # not straight away
    played = []
    for _ in range(200):
        clock.t += 2
        if clock.t % 120 == 0:
            lf.touch()  # someone hovers now and then, so no nap
        action = lf.tick(True, ALL)
        if action:
            played.append((clock.t, action))
    names = [a for _, a in played]
    assert set(names) <= ALL and len(set(names)) >= 3
    assert all(a != b for a, b in zip(names, names[1:], strict=False))
    gaps = [t2 - t1 for (t1, _), (t2, _) in zip(played, played[1:], strict=False)]
    assert min(gaps) >= 24


def test_naps_after_quiet_time_and_wakes_on_touch():
    lf, clock = life(nap_after=300)
    decisions = []
    while clock.t < 400:
        clock.t += 2
        decisions.append(lf.tick(True, set()))
    assert "nap" in decisions and lf.napping
    assert decisions.count("nap") == 1  # says it once, then stays asleep
    assert lf.touch() is True and not lf.napping
    assert lf.touch() is False


def test_nothing_happens_while_the_timer_runs():
    lf, clock = life(nap_after=300)
    for _ in range(300):
        clock.t += 2
        assert lf.tick(False, ALL) is None
    assert not lf.napping


# ---- in the pet window ------------------------------------------------------
def hover(widget, x, y):
    ev = QMouseEvent(
        QEvent.MouseMove, QPointF(x, y), widget.mapToGlobal(QPointF(x, y)), Qt.NoButton, Qt.NoButton, Qt.NoModifier
    )
    QApplication.sendEvent(widget, ev)


def test_rubbing_the_pup_pets_it(controller):
    pet = controller.pet
    got = []
    pet.petted.connect(lambda: got.append(True))
    cy = pet.sprite_rect.center().y()
    for x in [30, 45, 60, 45, 30, 45, 60, 45, 30, 45, 60]:
        hover(pet, x, cy)
    assert got == [True] and pet.being_petted
    assert pet._target_state() == "petted"
    # the controller logs it once and the pup's mood counts it
    pets = [r for r in controller.history.records if r["type"] == "pet"]
    assert len(pets) == 1 and "got 1 pet today" in controller.mood.reason


def test_one_shot_animation_plays_then_returns_to_idle(controller):
    pet = controller.pet
    pet.play("yawn")
    assert pet._target_state() == "yawn"
    pet._refresh_state()
    for _ in range(len(pet.sprites["yawn"])):
        pet._advance()
    assert pet._action is None and pet._target_state() == "mood_content"  # back to the idle pose


def test_nap_and_wake_up(controller):
    pet = controller.pet
    pet.life.napping = True
    assert pet._target_state() == "break"  # curled up
    pet.enterEvent(None)  # you hover over it
    assert not pet.life.napping and pet._target_state() == "yawn"


def test_idle_animations_can_be_turned_off(controller):
    controller.set_options({"idle_animations": False})
    assert controller.pet.idle_animations is False
    controller.pet.life.napping = True
    controller.pet._life_tick()  # with idle animations off, no naps either
    assert controller.pet.life.napping is False and controller.pet._target_state() == "mood_content"
