"""End to end: run the real app on a private D-Bus session and drive it with
the command-line remote, checking Do Not Disturb and session resume."""

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(shutil.which("dbus-daemon") is None, reason="needs dbus-daemon")


def wait_for(predicate, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.1)
    return False


@pytest.fixture
def env(tmp_path):
    bus = subprocess.Popen(
        ["dbus-daemon", "--session", "--nofork", "--print-address=1"], stdout=subprocess.PIPE, text=True
    )
    address = bus.stdout.readline().strip()
    runtime = tmp_path / "run"
    runtime.mkdir(mode=0o700)
    e = dict(os.environ)
    e.update(
        DBUS_SESSION_BUS_ADDRESS=address,
        XDG_RUNTIME_DIR=str(runtime),
        XDG_CONFIG_HOME=str(tmp_path / "config"),
        XDG_STATE_HOME=str(tmp_path / "state"),
        XDG_DATA_HOME=str(tmp_path / "data"),
        QT_QPA_PLATFORM="offscreen",
        PYTHONPATH=str(ROOT),
        POMODORO_PET_IDLE_POLL="0.3",
    )
    e.pop("WAYLAND_DISPLAY", None)
    e["XDG_SESSION_TYPE"] = "x11"
    log = tmp_path / "notifications.log"
    fake = subprocess.Popen([sys.executable, str(ROOT / "tests" / "fake_desktop.py"), str(log)], env=e)
    assert wait_for(lambda: log.exists() and "ready" in log.read_text())
    yield e, log, tmp_path
    fake.terminate()
    bus.terminate()
    fake.wait(5)
    bus.wait(5)


def cli(e, *args):
    r = subprocess.run([sys.executable, "-m", "pomodoro_pet", *args], env=e, capture_output=True, text=True, timeout=20)
    return r.returncode, r.stdout.strip()


def start_app(e):
    proc = subprocess.Popen([sys.executable, "-m", "pomodoro_pet"], env=e, stderr=subprocess.PIPE, text=True)
    assert wait_for(lambda: cli(e, "--status")[0] == 0), "app did not come up"
    return proc


def stop_app(proc):
    proc.send_signal(signal.SIGTERM)
    proc.wait(10)


def events(log):
    return [line for line in log.read_text().splitlines() if line in ("inhibit pomodoro-pet", "released")]


def test_remote_control_dnd_and_resume(env):
    e, log, tmp = env
    assert cli(e, "--status") == (1, "Pomodoro Pet is not running")

    app = start_app(e)
    try:
        assert cli(e, "--status") == (0, "Idle · 25:00")

        assert cli(e, "--intention", "Write tests", "--toggle")[0] == 0
        assert wait_for(lambda: events(log) == ["inhibit pomodoro-pet"])
        code, status = cli(e, "--status")
        assert status.startswith("Focus · round 1/4 · ") and status.endswith("left — Write tests")
        time.sleep(1.2)  # focused time for the round skipped below (paused time doesn't count)

        cli(e, "--pause")
        assert wait_for(lambda: events(log) == ["inhibit pomodoro-pet", "released"])
        assert "(paused)" in cli(e, "--status")[1]

        cli(e, "--start")
        assert wait_for(lambda: len(events(log)) == 3)

        cli(e, "--skip")  # into the short break: DND lifted before the notification
        assert wait_for(lambda: len(events(log)) == 4)
        lines = log.read_text().splitlines()
        last_release = max(i for i, line in enumerate(lines) if line == "released")
        last_notify = max(i for i, line in enumerate(lines) if line.startswith("notify"))
        assert last_release < last_notify
        assert cli(e, "--status")[1].startswith("Short break · ")
        history = [json.loads(line) for line in (tmp / "data" / "pomodoro-pet" / "history.jsonl").open()]
        assert history[-1]["type"] == "focus" and history[-1]["outcome"] == "skipped"
        assert history[-1]["intention"] == "Write tests" and history[-1]["seconds"] >= 1
        code, report = cli(e, "--stats")  # works straight from the log file
        assert code == 0 and report.startswith("Biscuit is ") and "Write tests" in report

        cli(e, "--toggle")  # pause the break
        assert cli(e)[0] == 0  # second launch just talks to the running one
    finally:
        stop_app(app)

    session = json.loads((tmp / "state" / "pomodoro-pet" / "session.json").read_text())
    assert session["state"] == "paused" and session["phase"] == "short_break"

    app = start_app(e)  # resumes where we left off
    try:
        assert cli(e, "--status")[1].startswith("Short break · ")
        assert cli(e, "--status")[1].endswith("(paused)")
        cli(e, "--reset")
        assert wait_for(lambda: not (tmp / "state" / "pomodoro-pet" / "session.json").exists())
    finally:
        stop_app(app)


def test_start_launches_and_survives_a_crash(env):
    e, log, tmp = env
    app = subprocess.Popen([sys.executable, "-m", "pomodoro_pet", "--start"], env=e)
    try:
        assert wait_for(lambda: cli(e, "--status")[1].startswith("Focus · round 1/4"))
        assert wait_for(lambda: events(log) == ["inhibit pomodoro-pet"])
    finally:
        app.kill()  # SIGKILL: no cleanup code runs
        app.wait(10)
    # Do Not Disturb isn't left stuck on...
    assert wait_for(lambda: events(log) == ["inhibit pomodoro-pet", "released"])
    # ...and the running session comes back on the next launch.
    session = json.loads((tmp / "state" / "pomodoro-pet" / "session.json").read_text())
    assert session["state"] == "running"
    app = start_app(e)
    try:
        status = cli(e, "--status")[1]
        assert status.startswith("Focus · round 1/4") and not status.endswith("(paused)")
    finally:
        stop_app(app)


def screensaver(e, method, *args):
    subprocess.run(
        ["gdbus", "call", "--session", "--dest", "org.freedesktop.ScreenSaver", "--object-path", "/ScreenSaver",
         "--method", f"org.freedesktop.ScreenSaver.{method}", *args],
        env=e, check=True, capture_output=True, timeout=10,
    )


@pytest.mark.skipif(shutil.which("gdbus") is None, reason="needs gdbus")
def test_idle_pauses_focus_and_welcomes_you_back(env):
    e, log, _ = env
    app = subprocess.Popen([sys.executable, "-m", "pomodoro_pet", "--start"], env=e)
    try:
        assert wait_for(lambda: cli(e, "--status")[1].startswith("Focus · round 1/4"))
        time.sleep(1)
        screensaver(e, "SetIdle", "200000")  # no input for 200 s (> 3 min)
        assert wait_for(lambda: "(paused)" in cli(e, "--status")[1])
        # the idle time before the pause doesn't count as focus
        assert cli(e, "--status")[1].startswith("Focus · round 1/4 · 25:00 left (paused)")
        assert wait_for(lambda: "released" in events(log))  # Do Not Disturb lifted too

        screensaver(e, "SetIdle", "0")  # back at the keyboard
        assert wait_for(lambda: "notify Welcome back!" in log.read_text())
        assert "(paused)" in cli(e, "--status")[1]  # waits for you to choose
        if shutil.which("notify-send"):  # with libnotify the notification has buttons
            assert wait_for(lambda: "actions resume credit" in log.read_text())
            subprocess.run(
                ["gdbus", "call", "--session", "--dest", "org.freedesktop.Notifications", "--object-path",
                 "/org/freedesktop/Notifications", "--method", "org.freedesktop.Notifications.InvokeLast", "credit"],
                env=e, check=True, capture_output=True, timeout=10,
            )
            # "I was working, count it": ~200 s away credited as focus, and running again
            assert wait_for(lambda: "(paused)" not in cli(e, "--status")[1])
            minutes_left = int(cli(e, "--status")[1].split(" · ")[2].split(":")[0])
            assert 20 <= minutes_left <= 21
            cli(e, "--pause")

        cli(e, "--start")
        screensaver(e, "SetLocked", "true")  # locking the screen pauses right away
        assert wait_for(lambda: "(paused)" in cli(e, "--status")[1])
    finally:
        stop_app(app)


def seed_session_ending_soon(tmp, seconds=2.0, **config):
    """A running focus round that ends `seconds` after the app starts."""
    now = time.time()
    state = tmp / "state" / "pomodoro-pet"
    state.mkdir(parents=True, exist_ok=True)
    (state / "session.json").write_text(json.dumps({
        "state": "running", "phase": "focus", "round": 1, "remaining": seconds,
        "saved_at": now, "ends_at": now + seconds + 1.5,  # + startup time
    }))
    if config:
        cfg = tmp / "config" / "pomodoro-pet"
        cfg.mkdir(parents=True, exist_ok=True)
        (cfg / "config.json").write_text(json.dumps(config))


def click_last_notification(e, action):
    subprocess.run(
        ["gdbus", "call", "--session", "--dest", "org.freedesktop.Notifications", "--object-path",
         "/org/freedesktop/Notifications", "--method", "org.freedesktop.Notifications.InvokeLast", action],
        env=e, check=True, capture_output=True, timeout=10,
    )


@pytest.mark.skipif(not shutil.which("gdbus") or not shutil.which("notify-send"), reason="needs gdbus + notify-send")
def test_keep_going_button_turns_the_break_into_overtime(env):
    e, log, tmp = env
    seed_session_ending_soon(tmp)
    app = start_app(e)
    try:
        assert wait_for(lambda: cli(e, "--status")[1].startswith("Short break"))
        assert wait_for(lambda: "actions keep" in log.read_text())
        click_last_notification(e, "keep")
        assert wait_for(lambda: "overtime" in cli(e, "--status")[1])
        assert cli(e, "--status")[1].startswith("Focus · round 1/4 · +00:0")
        time.sleep(1.2)  # a little overtime to log
        cli(e, "--skip")  # = take my break
        assert wait_for(lambda: cli(e, "--status")[1].startswith("Short break · 05:00"))
    finally:
        stop_app(app)
    history = [json.loads(line) for line in (tmp / "data" / "pomodoro-pet" / "history.jsonl").open()]
    assert [(r["type"], r["outcome"]) for r in history] == [("focus", "completed"), ("focus", "overtime")]


@pytest.mark.skipif(not shutil.which("gdbus") or not shutil.which("notify-send"), reason="needs gdbus + notify-send")
def test_flow_mode_counts_overtime_until_take_break(env):
    e, log, tmp = env
    seed_session_ending_soon(tmp, flow_mode=True)
    app = start_app(e)
    try:
        assert wait_for(lambda: "overtime" in cli(e, "--status")[1])
        assert wait_for(lambda: "notify Focus round done! 🌊" in log.read_text())
        time.sleep(1.2)
        assert "+00:00" not in cli(e, "--status")[1]  # counting up
        click_last_notification(e, "break")  # "Take break now"
        assert wait_for(lambda: cli(e, "--status")[1].startswith("Short break · "))
    finally:
        stop_app(app)
