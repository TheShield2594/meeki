import time

from pomodoro_pet.hooks import README, ensure_hooks_dir, hook_scripts


def wait_lines(path, n, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if path.exists() and len(path.read_text().splitlines()) >= n:
            return path.read_text().splitlines()
        time.sleep(0.05)
    return path.read_text().splitlines() if path.exists() else []


def test_hook_scripts_get_events_and_details(controller, tmp_path):
    hooks = ensure_hooks_dir()
    assert (hooks / "README.txt").read_text() == README
    log = tmp_path / "hook.log"
    script = hooks / "10-log"
    script.write_text(
        "#!/bin/sh\n"
        f'echo "$1 $POMODORO_PHASE round=$POMODORO_ROUND out=$POMODORO_OUTCOME i=$POMODORO_INTENTION" >> "{log}"\n'
    )
    script.chmod(0o755)
    (hooks / "notes.txt").write_text("not executable: ignored")
    assert hook_scripts() == [script]

    controller.set_intention("Write tests")
    t = controller.timer
    t.start()
    wait_lines(log, 1)
    t.pause()
    wait_lines(log, 2)
    t.start()
    wait_lines(log, 3)
    time.sleep(1.1)
    t.skip()
    lines = wait_lines(log, 5)
    assert lines[0] == "phase_start focus round=1 out= i=Write tests"
    assert lines[1].startswith("paused focus")
    assert lines[2].startswith("resumed focus")
    assert "phase_end focus round=1 out=skipped i=Write tests" in lines
    assert any(line.startswith("phase_start short_break") for line in lines)


def test_hooks_can_be_switched_off(controller, tmp_path):
    hooks = ensure_hooks_dir()
    log = tmp_path / "hook.log"
    script = hooks / "log"
    script.write_text(f'#!/bin/sh\necho "$1" >> "{log}"\n')
    script.chmod(0o755)
    controller.set_options({"hooks_enabled": False})
    controller.timer.start()
    time.sleep(0.5)
    assert not log.exists()
