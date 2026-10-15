"""Quick controls on the pet: scroll on the bubble, right-click menu, keyboard
navigation in the popover, and stepping aside for fullscreen apps."""

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt, QTimer
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QMenu


def wheel(widget, pos, dy):
    ev = QWheelEvent(QPointF(pos), widget.mapToGlobal(QPointF(pos)), QPoint(0, 0), QPoint(0, dy),
                     Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
    QApplication.sendEvent(widget, ev)


def test_scroll_on_bubble_changes_focus_length_when_idle(controller):
    pet = controller.pet
    on_bubble = pet.bubble_rect.center()
    wheel(pet, on_bubble, 120)
    wheel(pet, on_bubble, 120)
    assert controller.cfg["focus_minutes"] == 27 and controller.timer.remaining_seconds() == 27 * 60
    for _ in range(4):
        wheel(pet, on_bubble, 30)  # a touchpad: four small steps make one notch
    assert controller.cfg["focus_minutes"] == 28
    wheel(pet, pet.sprite_rect.center(), 120)  # not on the bubble: ignored
    assert controller.cfg["focus_minutes"] == 28


def test_scroll_on_bubble_adjusts_a_running_phase(controller):
    controller.timer.start()
    before = controller.timer.remaining_seconds()
    wheel(controller.pet, controller.pet.bubble_rect.center(), -120)
    assert before - controller.timer.remaining_seconds() in (60, 61)
    assert controller.cfg["focus_minutes"] == 25  # the setting itself is untouched


def menu_texts(controller):
    """Open the right-click menu, read its entries, close it."""
    texts = []

    def grab():
        menu = next(w for w in QApplication.topLevelWidgets() if isinstance(w, QMenu) and w.isVisible())
        texts.extend((a.text(), a.isEnabled()) for a in menu.actions() if not a.isSeparator())
        menu.close()

    QTimer.singleShot(50, grab)
    controller._show_menu(QPoint(10, 10))
    return dict(texts)


def test_right_click_menu_matches_the_timer_state(controller):
    idle = menu_texts(controller)
    assert idle["Start"] and not idle["Skip"] and not idle["Reset"] and idle["Settings"] and idle["Quit"]
    controller.timer.start()
    running = menu_texts(controller)
    assert running["Pause"] and running["Skip"] and running["Add 5 minutes"]


def test_fullscreen_app_hides_and_restores_the_pet(controller):
    pet = controller.pet
    assert pet.isVisible()
    controller.service.fullscreen_changed.emit(True)
    assert not pet.isVisible()
    controller.service.fullscreen_changed.emit(False)
    assert pet.isVisible()
    controller.set_options({"hide_in_fullscreen": False})
    controller.service.fullscreen_changed.emit(True)
    assert pet.isVisible()  # setting off: stays put


def test_popover_keyboard_navigation(controller):
    pop = controller.popover
    expected = [pop.intention, *pop.spins.values(), pop.start_btn, pop.skip_btn, pop.reset_btn,
                pop.stats_btn, pop.settings_btn]
    # walk Qt's focus chain (it also visits internal children, e.g. a spin box's line edit)
    chain, w = [], pop.intention
    for _ in range(80):
        if w in expected and w not in chain:
            chain.append(w)
        w = w.nextInFocusChain()
    assert chain == expected  # Tab goes top to bottom
    # Alt+letter mnemonics on the buttons follow the state
    assert pop.start_btn.text() == "&Start" and pop.skip_btn.text() == "S&kip"
    controller.timer.start()
    assert pop.start_btn.text() == "&Pause"
    controller.timer.pause()
    assert pop.start_btn.text() == "Res&ume"


def test_theme_follows_dark_palette(controller, qapp):
    from PySide6.QtGui import QColor, QPalette

    from pomodoro_pet import theme

    old = qapp.palette()
    try:
        dark = QPalette(old)
        dark.setColor(QPalette.Window, QColor("#1f2023"))
        dark.setColor(QPalette.Highlight, QColor("#3daee9"))  # Plasma's Breeze blue
        qapp.setPalette(dark)
        if qapp.styleHints().colorScheme() == Qt.ColorScheme.Unknown:
            assert theme.is_dark()
            assert theme.bubble_colors().fill.lightness() < 100
        assert theme.accent().name() == "#3daee9"
        QApplication.sendEvent(controller.pet, QEvent(QEvent.PaletteChange))  # repaint, no crash
    finally:
        qapp.setPalette(old)
