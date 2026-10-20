"""Update notices: version comparison, and the checker against a local HTTP server."""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from PySide6.QtCore import QCoreApplication, QEvent

from pomodoro_pet import updates
from pomodoro_pet.updates import UpdateChecker, is_newer, parse_version


def test_parse_version():
    assert parse_version("v0.3.0") == (0, 3, 0)
    assert parse_version("1.10") == (1, 10)
    assert parse_version("v1.0.0-rc1") is None
    assert parse_version("nightly") is None


@pytest.mark.parametrize(("latest", "current", "newer"), [
    ("0.3.0", "0.2.0", True),
    ("v0.2.1", "0.2.0", True),
    ("0.10.0", "0.9.0", True),
    ("0.2", "0.2.0", False),
    ("0.2.0", "0.2.0", False),
    ("0.1.9", "0.2.0", False),
    ("garbage", "0.2.0", False),
])
def test_is_newer(latest, current, newer):
    assert is_newer(latest, current) is newer


@pytest.fixture
def server():
    """Serves `server.reply` (status, body) for every GET; counts the requests."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            srv.hits += 1
            status, body = srv.reply
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body.encode())

        def log_message(self, *_args):
            pass

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    srv.hits = 0
    srv.reply = (200, "{}")
    srv.url = f"http://127.0.0.1:{srv.server_port}/releases/latest"
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def release(tag):
    return json.dumps({"tag_name": tag, "html_url": f"https://example.invalid/releases/{tag}"})


def run_check(qapp, checker, server):
    hits = server.hits if server else 0
    checker.check()
    deadline = time.monotonic() + 10
    while checker.busy and time.monotonic() < deadline:
        # Only the checker's queued reply: a full processEvents() here can deliver stale
        # events to the half-torn-down controllers of earlier tests and crash.
        QCoreApplication.sendPostedEvents(checker, QEvent.Type.MetaCall)
        time.sleep(0.01)
    assert not checker.busy, "the check never finished"
    return server.hits - hits if server else 0


@pytest.fixture
def checker(server, tmp_path, monkeypatch):
    monkeypatch.setattr(updates, "__version__", "0.2.0")
    c = UpdateChecker(server.url, tmp_path / "updates.json")
    seen = []
    c.update_available.connect(lambda version, page: seen.append((version, page)))
    c.seen = seen
    yield c
    c.stop()


def test_announces_a_new_release_once(qapp, server, checker):
    server.reply = (200, release("v0.3.0"))
    assert run_check(qapp, checker, server) == 1
    assert checker.seen == [("0.3.0", "https://example.invalid/releases/v0.3.0")]
    assert checker.latest == ("0.3.0", "https://example.invalid/releases/v0.3.0")
    run_check(qapp, checker, server)
    assert len(checker.seen) == 1  # same version: not again
    server.reply = (200, release("v0.4.0"))
    run_check(qapp, checker, server)
    assert checker.seen[-1][0] == "0.4.0"


def test_reply_is_handled_on_the_main_thread(qapp, server, checker):
    threads = []
    checker.update_available.connect(lambda *_: threads.append(threading.current_thread()))
    server.reply = (200, release("v0.3.0"))
    run_check(qapp, checker, server)
    assert threads == [threading.main_thread()]


def test_quiet_when_up_to_date_or_failing(qapp, server, checker):
    server.reply = (200, release("v0.2.0"))
    run_check(qapp, checker, server)
    server.reply = (404, '{"message": "Not Found"}')  # no releases yet
    run_check(qapp, checker, server)
    server.reply = (200, "not json")
    run_check(qapp, checker, server)
    assert checker.seen == [] and checker.latest is None
    assert updates.load_state(checker.state_file)["last_check"] > 0


def test_offline_check_is_retried_next_hour(qapp, checker, monkeypatch):
    monkeypatch.setattr(checker, "url", "http://127.0.0.1:9/")  # nothing listens on the discard port
    run_check(qapp, checker, None)
    assert "last_check" not in updates.load_state(checker.state_file)


def test_compares_with_the_running_version(monkeypatch):
    monkeypatch.setattr(updates, "__version__", "1.0.0")
    assert not is_newer("0.9.0") and is_newer("1.0.1")


def test_checks_at_most_once_a_day(qapp, server, checker):
    server.reply = (200, release("v0.2.0"))
    checker.check_if_due()
    run_check(qapp, checker, server)  # waits for the first one (a second check() is a no-op meanwhile)
    hits = server.hits
    checker.check_if_due()
    assert not checker.busy and server.hits == hits  # checked just now: not due
    state = updates.load_state(checker.state_file)
    state["last_check"] = time.time() - updates.CHECK_EVERY - 1
    updates.save_state(state, checker.state_file)
    checker.check_if_due()
    assert checker.busy
    run_check(qapp, checker, server)


def test_setting_turns_the_checker_on_and_off(controller):
    assert controller.updates is None  # tests start with it off
    controller.set_options({"check_for_updates": True})
    assert controller.updates is not None
    controller.set_options({"check_for_updates": False})
    assert controller.updates is None


def test_update_notice_from_the_pup(controller, monkeypatch):
    said = []
    monkeypatch.setattr(controller, "say", said.append)
    controller._on_update_available("9.9.9", "https://example.invalid")
    assert said and "9.9.9" in said[0]


def test_first_check_waits_and_stop_cancels_it(checker):
    checker.start()
    assert checker._first.isActive() and not checker.busy
    checker.stop()
    assert not checker._first.isActive() and not checker._timer.isActive()
