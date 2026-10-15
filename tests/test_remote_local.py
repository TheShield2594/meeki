"""The local-socket remote control used where there's no D-Bus (macOS)."""

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


def test_rejects_unknown_methods(monkeypatch, qapp):
    from PySide6.QtNetwork import QLocalSocket

    from pomodoro_pet.remote import PetService

    runtime = tempfile.mkdtemp(prefix="pp")
    monkeypatch.setenv("XDG_RUNTIME_DIR", runtime)
    service = PetService(lambda: "")
    positions = []
    service.position_reported.connect(lambda x, y: positions.append((x, y)))
    assert service._listen_local()
    sock = QLocalSocket()
    sock.connectToServer(os.path.join(runtime, "pomodoro-pet.sock"))
    reply = b""
    try:
        for _ in range(200):
            qapp.processEvents()
            if sock.state() == QLocalSocket.ConnectedState:
                break
            time.sleep(0.01)
        sock.write(b'["reportPosition", "1,2"]\n')
        sock.flush()
        for _ in range(200):
            qapp.processEvents()
            sock.waitForReadyRead(10)
            reply += bytes(sock.readAll())
            if reply.endswith(b"\n"):
                break
    finally:
        service.unregister()
    assert b'"ok": false' in reply
    assert positions == []
