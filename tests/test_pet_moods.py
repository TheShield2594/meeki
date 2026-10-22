"""The idle pose follows the mood: a sprite per mood level when the sprite set
has one, else the classic idle / sulky poses."""

import shutil

import pytest

from pomodoro_pet.assets import MOOD_LEVELS, asset, load_sprites


@pytest.mark.parametrize("level", MOOD_LEVELS)
def test_each_mood_has_its_own_idle_pose(controller, level):
    pet = controller.pet
    assert pet.sprites.get(f"mood_{level}"), "bundled sprites include every mood"
    pet.set_mood(level)
    assert pet._anim_state == f"mood_{level}"


def test_running_timer_wins_over_the_mood(controller):
    pet = controller.pet
    pet.set_mood("overjoyed")
    controller.timer.start()
    assert pet._anim_state == "focusing"
    controller.timer.reset()
    assert pet._anim_state == "mood_overjoyed"


def test_sprite_sets_without_mood_poses_fall_back(controller):
    pet = controller.pet
    pet.set_sprites({k: v for k, v in pet.sprites.items() if not k.startswith("mood_")})
    pet.set_mood("sad")
    assert pet._anim_state == "sulky"
    pet.set_mood("happy")
    assert pet._anim_state == "idle"  # with hearts painted on top


def test_custom_idle_art_does_not_borrow_bundled_mood_poses(tmp_path):
    bundled = asset("sprites")
    shutil.copytree(bundled / "idle", tmp_path / "idle")
    assert "mood_happy" not in load_sprites(str(tmp_path))
    shutil.copytree(bundled / "mood_happy", tmp_path / "mood_happy")
    assert "mood_happy" in load_sprites(str(tmp_path))
    assert "mood_happy" in load_sprites(str(tmp_path / "nothing-here"))  # no custom idle: bundled set
