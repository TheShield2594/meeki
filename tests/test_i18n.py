import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest

from pomodoro_pet import i18n

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("i18n_tool", ROOT / "tools" / "i18n.py")
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)

GERMAN = r'''
msgid ""
msgstr ""
"Content-Type: text/plain; charset=UTF-8\n"
"Plural-Forms: nplurals=2; plural=(n != 1);\n"

msgid "Focus"
msgstr "Fokus"

#, python-brace-format
msgid "{n} round"
msgid_plural "{n} rounds"
msgstr[0] "{n} Runde"
msgstr[1] "{n} Runden"

#, fuzzy
msgid "Reset"
msgstr "Zurücksetzen (unsicher)"

msgid "Skip"
msgstr ""

msgid "Ready when you are"
msgstr "Bereit, "
"wenn du es bist"
'''


@pytest.fixture
def german(tmp_path, monkeypatch):
    po = tmp_path / "de.po"
    po.write_text(GERMAN, encoding="utf-8")
    tool.write_mo(tool.parse_po(po), tmp_path / "locale" / "de" / "LC_MESSAGES" / "pomodoro-pet.mo")
    monkeypatch.setenv("POMODORO_PET_LOCALEDIR", str(tmp_path / "locale"))
    monkeypatch.setenv("LANGUAGE", "de")
    i18n.reload()
    yield
    monkeypatch.undo()
    i18n.reload()


def test_compiled_translation_is_used(german):
    assert i18n._("Focus") == "Fokus"
    assert i18n.ngettext("{n} round", "{n} rounds", 1).format(n=1) == "1 Runde"
    assert i18n.ngettext("{n} round", "{n} rounds", 3).format(n=3) == "3 Runden"
    assert i18n._("Ready when you are") == "Bereit, wenn du es bist"  # multi-line msgstr
    assert i18n._("Reset") == "Reset"  # fuzzy entries are skipped
    assert i18n._("Skip") == "Skip"  # untranslated falls back to English
    assert i18n._("Not in the catalog") == "Not in the catalog"


def test_mo_matches_gnu_msgfmt(tmp_path):
    if not shutil.which("msgfmt"):
        pytest.skip("needs gettext's msgfmt")
    po = tmp_path / "de.po"
    po.write_text(GERMAN, encoding="utf-8")
    ours = tmp_path / "ours.mo"
    tool.write_mo(tool.parse_po(po), ours)
    theirs = tmp_path / "theirs.mo"
    subprocess.run(["msgfmt", "-o", str(theirs), str(po)], check=True)
    import gettext

    with open(ours, "rb") as a, open(theirs, "rb") as b:
        ta, tb = gettext.GNUTranslations(a), gettext.GNUTranslations(b)
    for msg in ("Focus", "Ready when you are", "Reset", "Skip"):
        assert ta.gettext(msg) == tb.gettext(msg)
    assert ta.ngettext("{n} round", "{n} rounds", 5) == tb.ngettext("{n} round", "{n} rounds", 5)


def test_template_is_up_to_date():
    if not shutil.which("xgettext"):
        pytest.skip("needs xgettext")
    assert tool.check() == 0, "run: python tools/i18n.py extract"


def test_popover_shows_translated_labels(german, controller):
    from pomodoro_pet.popover import Popover

    pop = Popover(controller.timer, controller.pet)
    pop.refresh()
    assert pop.status.text() == "Bereit, wenn du es bist"
