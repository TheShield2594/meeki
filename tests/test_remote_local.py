"""The local-socket remote control used where there's no D-Bus (macOS)."""

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run_client(qapp, env, *args):
    """Run `pomodoro-pet ARGS` while this process keeps serving the socket."""
    proc = subprocess.Popen(
        [sys.executable, "-m", "pomodoro_pet", *args], env=env, cwd=ROOT,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    end = time.monotonic() + 20
    while proc.poll() is None and time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.01)
    out, err = proc.communicate(timeout=5)
    return proc.returncode, out, err


def test_commands_over_local_socket(monkeypatch, qapp):
    runtime = Path(tempfile.mkdtemp(prefix="pp"))  # short: macOS caps socket paths at 104 bytes
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/nonexistent")
    from PySide6.QtCore import QLockFile

    from pomodoro_pet.remote import PetService, local_socket_path

    lock = QLockFile(str(runtime / "pomodoro-pet.lock"))  # what a running pet holds
    assert lock.tryLock(100)
    service = PetService(lambda: "Focus · round 1/4 · 25:00 left")
    commands, intentions = [], []
    service.command.connect(commands.append)
    service.intention_set.connect(intentions.append)
    assert service.register()
    assert os.path.exists(local_socket_path())
    env = dict(os.environ, PYTHONPATH=str(ROOT), QT_QPA_PLATFORM="offscreen")
    try:
        code, out, err = run_client(qapp, env, "--status")
        assert code == 0, err
        assert out.strip() == "Focus · round 1/4 · 25:00 left"

        code, _out, err = run_client(qapp, env, "--intention", "Write report", "--toggle")
        assert code == 0, err
        assert intentions == ["Write report"]
        assert commands == ["toggle"]
    finally:
        service.unregister()
        lock.unlock()


class FakeSocket:
    """Stands in for the server side of a QLocalSocket connection."""

    def __init__(self, line: bytes):
        self.line, self.written, self.closed = line, b"", False

    def canReadLine(self):  # noqa: N802
        return True

    def readLine(self):  # noqa: N802
        return self.line

    def write(self, data):
        self.written += data

    def flush(self):
        pass

    def disconnectFromServer(self):  # noqa: N802
        self.closed = True


def test_local_socket_requests():
    from pomodoro_pet.remote import PetService

    service = PetService(lambda: "Idle")
    positions, commands = [], []
    service.position_reported.connect(lambda x, y: positions.append((x, y)))
    service.command.connect(commands.append)

    def ask(line: bytes) -> dict:
        sock = FakeSocket(line)
        service._serve_local(sock)
        assert sock.closed
        return json.loads(sock.written)

    assert ask(b'["Status"]\n') == {"ok": True, "result": "Idle"}
    assert ask(b'["Skip"]\n') == {"ok": True, "result": ""}
    assert commands == ["skip"]
    # KWin-only methods, bad arguments and garbage are refused
    assert ask(b'["reportPosition", "1,2"]\n')["ok"] is False
    assert ask(b'["SetIntention", 5]\n')["ok"] is False
    assert ask(b'{"Status": 1}\n')["ok"] is False
    assert ask(b"not json\n")["ok"] is False
    assert positions == []
