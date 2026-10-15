"""Runs the real KWin script in a QJSEngine against a mock KWin `workspace`."""

import json

import pytest

QJSEngine = pytest.importorskip("PySide6.QtQml").QJSEngine

from pomodoro_pet.wayland import _KWIN_SCRIPT, OVERLAY_TITLE  # noqa: E402

MOCK = r"""
var calls = [];
function sig() {
    var h = [];
    return {connect: function (f) { h.push(f); }, fire: function () { for (var i = 0; i < h.length; i++) h[i](); }};
}
var nextId = 1;
function mkwin(cls, caption, geo, extra) {
    var w = {resourceClass: cls, resourceName: cls, caption: caption, internalId: "{" + (nextId++) + "}",
             popupWindow: false, frameGeometry: geo, keepAbove: false, fullScreen: false, minimized: false,
             onAllDesktops: false, frameGeometryChanged: sig(), fullScreenChanged: sig()};
    for (var k in (extra || {})) w[k] = extra[k];
    return w;
}
var added = sig(), activated = sig();
var all = [];
var workspace = {
    activeWindow: null,
    windowList: function () { return all; },
    windowAdded: added, windowActivated: activated,
};
function callDBus(service, path, iface, method, arg) { calls.push(method + ":" + arg); }
"""


class Kwin:
    def __init__(self, place=True):
        self.engine = QJSEngine()
        cfg = {
            "appId": "pomodoro-pet", "caption": "Pomodoro Pet", "overlayCaption": OVERLAY_TITLE,
            "keepAbove": True, "place": place, "x": 100, "y": 200,
            "service": "s", "path": "/p", "iface": "i",
        }
        self.run(MOCK)
        # windowAdded passes the window to the handler; emulate that
        self.run("added.fire = function (w) { for (var i = 0; i < added._h.length; i++) added._h[i](w); };"
                 "added._h = []; added.connect = function (f) { added._h.push(f); };")
        self.run("activated._h = []; activated.connect = function (f) { activated._h.push(f); };"
                 "activated.fire = function () { for (var i = 0; i < activated._h.length; i++) activated._h[i](); };")
        self.run(_KWIN_SCRIPT.replace("__CONFIG__", json.dumps(cfg)))

    def run(self, code):
        result = self.engine.evaluate(code)
        assert not result.isError(), result.toString()
        return result

    def value(self, expr):
        return json.loads(self.run(f"JSON.stringify({expr})").toString())


def new_pet(k, var="pet", x=5, y=6):
    k.run(f"var {var} = mkwin('pomodoro-pet', 'Pomodoro Pet', {{x: {x}, y: {y}, width: 180, height: 130}});"
          f"added.fire({var});")


def test_places_pet_on_all_desktops_and_reports_position():
    k = Kwin()
    new_pet(k)
    assert k.value("[pet.keepAbove, pet.onAllDesktops, pet.skipTaskbar]") == [True, True, True]
    assert k.value("[pet.frameGeometry.x, pet.frameGeometry.y]") == [100, 200]
    assert "reportPosition:100,200" in k.value("calls")


def test_x11_mode_does_not_move_the_window():
    k = Kwin(place=False)
    new_pet(k)
    assert k.value("[pet.frameGeometry.x, pet.onAllDesktops]") == [5, True]


def test_overlay_kept_above_and_not_moved():
    k = Kwin()
    k.run(f"var ov = mkwin('pomodoro-pet', {json.dumps(OVERLAY_TITLE)}, {{x: 0, y: 0, width: 800, height: 600}});"
          "added.fire(ov);")
    assert k.value("[ov.keepAbove, ov.skipTaskbar, ov.frameGeometry.x]") == [True, True, 0]


def test_fullscreen_window_over_the_pet_is_reported():
    k = Kwin()
    new_pet(k)
    k.run("var video = mkwin('mpv', 'video', {x: 0, y: 0, width: 1920, height: 1080});"
          "added.fire(video); video.fullScreen = true; workspace.activeWindow = video; video.fullScreenChanged.fire();")
    assert k.value("calls").count("reportFullscreen:1") == 1
    k.run("var editor = mkwin('kate', 'kate', {x: 0, y: 0, width: 800, height: 600});"
          "added.fire(editor); workspace.activeWindow = editor; activated.fire();")
    assert k.value("calls")[-1] == "reportFullscreen:0"


def test_fullscreen_on_another_monitor_is_ignored():
    k = Kwin()
    new_pet(k)
    k.run("var game = mkwin('game', 'game', {x: 1920, y: 0, width: 1920, height: 1080});"
          "added.fire(game); game.fullScreen = true; workspace.activeWindow = game; activated.fire();")
    assert "reportFullscreen:1" not in k.value("calls")


def test_reshown_pet_returns_to_its_last_position():
    k = Kwin()
    new_pet(k)
    k.run("pet.frameGeometry = {x: 640, y: 480, width: 180, height: 130}; pet.frameGeometryChanged.fire();")
    new_pet(k, "pet2")  # hidden for a fullscreen app, shown again = a new window on Wayland
    assert k.value("[pet2.frameGeometry.x, pet2.frameGeometry.y]") == [640, 480]
