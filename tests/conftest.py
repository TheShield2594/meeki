import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def controller(tmp_path, monkeypatch, qapp):
    """A full in-process app (offscreen) with its files in tmp_path."""
    for var, sub in (("XDG_CONFIG_HOME", "config"), ("XDG_STATE_HOME", "state"), ("XDG_DATA_HOME", "data")):
        monkeypatch.setenv(var, str(tmp_path / sub))
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/nonexistent")  # keep off the real bus
    from pomodoro_pet.app import Controller
    from pomodoro_pet.config import Config

    c = Controller(qapp, Config().load())
    yield c
    c.shutdown()
    c.pet.close()
