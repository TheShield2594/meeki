"""Settings window <-> controller <-> popover, in-process (offscreen Qt)."""

import json

import pytest


@pytest.fixture
def controller(controller):
    controller.show_settings()
    yield controller
    controller.settings_window.close()


def test_settings_window_shows_current_values(controller):
    w = controller.settings_window.w
    assert w["focus_minutes"].value() == 25
    assert w["flow_mode"].currentData() is False
    assert w["break_enforcement"].currentData() == "nudge"
    assert w["pet_name"].text() == "Biscuit"
    assert w["scale"].value() == 100


def test_changes_apply_live_and_sync_the_popover(controller):
    c, w = controller, controller.settings_window.w
    w["focus_minutes"].setValue(50)
    assert c.cfg["focus_minutes"] == 50 and c.timer.remaining_seconds() == 50 * 60
    assert c.popover.spins["focus_minutes"].value() == 50
    w["flow_mode"].setCurrentIndex(w["flow_mode"].findData(True))
    assert c.cfg["flow_mode"] is True and c.timer.flow is True
    w["minute_warning"].setChecked(False)
    assert c.timer.warn_seconds == 0
    w["idle_minutes"].setValue(10)
    assert c.activity.away_after == 600
    w["auto_advance"].setChecked(False)
    assert c.timer.auto_advance is False

    c.popover.spins["rounds"].setValue(6)  # the other direction
    assert w["rounds"].value() == 6 and c.timer.rounds == 6


def test_pet_size_and_name_apply_live(controller):
    c, w = controller, controller.settings_window.w
    width = c.pet.width()
    w["scale"].setValue(150)
    assert c.cfg["scale"] == 1.5 and c.pet.width() > width * 1.4
    w["pet_name"].setText("Mochi")
    w["pet_name"].editingFinished.emit()
    assert c.cfg["pet_name"] == "Mochi"
    assert c.popover.mood_label.text().startswith("Mochi is ")
    assert c.overlay.pet_name == "Mochi"


def test_invalid_values_are_sanitized(controller):
    controller.set_options({"idle_minutes": 999, "break_enforcement": "explode", "scale": 0})
    assert controller.cfg["idle_minutes"] == 60
    assert controller.cfg["break_enforcement"] == "nudge"
    assert controller.cfg["scale"] == 0.5


def test_restart_note_only_when_backend_settings_change(controller):
    sw = controller.settings_window
    label = sw._restart_label
    assert label.isHidden()
    sw.w["backend"].setCurrentIndex(sw.w["backend"].findData("xcb"))
    assert not label.isHidden() and controller.cfg["backend"] == "xcb"
    sw.w["backend"].setCurrentIndex(sw.w["backend"].findData("auto"))
    assert label.isHidden()


def test_settings_are_saved(controller, tmp_path):
    controller.settings_window.w["sound"].setChecked(False)
    controller.settings_window.w["break_enforcement"].setCurrentIndex(1)  # Dim the screen
    controller.shutdown()
    saved = json.loads((tmp_path / "config" / "pomodoro-pet" / "config.json").read_text())
    assert saved["sound"] is False and saved["break_enforcement"] == "overlay"
