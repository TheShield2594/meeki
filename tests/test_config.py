import json

from pomodoro_pet.config import DEFAULTS, Config, config_path


def test_missing_file_gives_defaults(tmp_path):
    cfg = Config(tmp_path / "config.json").load()
    assert cfg.data == DEFAULTS


def test_roundtrip_and_sanitize(tmp_path):
    path = tmp_path / "sub" / "config.json"
    cfg = Config(path).load()
    cfg["focus_minutes"] = 50
    cfg["position"] = {"x": 10, "y": -20}
    cfg.save()
    assert Config(path).load()["position"] == {"x": 10, "y": -20}

    path.write_text(json.dumps({"focus_minutes": 9999, "rounds": "x", "backend": "nope", "unknown": 1}))
    loaded = Config(path).load()
    assert loaded["focus_minutes"] == 240
    assert loaded["rounds"] == 4
    assert loaded["backend"] == "auto"
    assert "unknown" not in loaded.data


def test_corrupt_file(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{not json")
    assert Config(path).load().data == DEFAULTS


def test_xdg_config_home(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert config_path() == tmp_path / "pomodoro-pet" / "config.json"


def test_session_file_roundtrip(tmp_path):
    from pomodoro_pet.config import load_session, save_session

    path = tmp_path / "state" / "session.json"
    assert load_session(path) is None
    save_session({"state": "running", "phase": "focus"}, path)
    assert load_session(path) == {"state": "running", "phase": "focus"}
    save_session(None, path)  # nothing to resume -> file removed
    assert not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("garbage")
    assert load_session(path) is None


def test_stale_lock_detection_looks_at_the_real_process(tmp_path):
    import subprocess
    import sys

    from pomodoro_pet.app import lock_owner_is_pet

    class FakeLock:
        def __init__(self, pid):
            self.pid = pid

        def getLockInfo(self):  # noqa: N802
            return (self.pid, "host", "python3")

    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    pet = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", "-m", "pomodoro_pet"])
    try:
        assert lock_owner_is_pet(FakeLock(other.pid)) is False  # a reused PID: stale
        assert lock_owner_is_pet(FakeLock(pet.pid)) is True
        assert lock_owner_is_pet(FakeLock(999_999_999)) is False  # long gone
        assert lock_owner_is_pet(FakeLock("garbage")) is True  # can't tell
    finally:
        other.kill()
        pet.kill()
