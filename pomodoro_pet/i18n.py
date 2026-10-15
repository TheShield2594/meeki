"""Translations (gettext).

User-visible strings are wrapped in _() / ngettext(). Translations live in
po/<lang>.po; `python tools/i18n.py compile` turns them into
build/locale/<lang>/LC_MESSAGES/pomodoro-pet.mo, and packages install those
to /usr/share/locale. The language follows LANGUAGE / LC_ALL / LC_MESSAGES /
LANG like every other desktop app; English is the fallback.
"""

from __future__ import annotations

import gettext
import os
from pathlib import Path

DOMAIN = "pomodoro-pet"


def _locale_dirs() -> list[str | None]:
    dirs: list[str | None] = []
    if os.environ.get("POMODORO_PET_LOCALEDIR"):
        dirs.append(os.environ["POMODORO_PET_LOCALEDIR"])
    dirs.append(str(Path(__file__).resolve().parent.parent / "build" / "locale"))  # source checkout
    data_home = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    dirs.append(os.path.join(data_home, "locale"))
    dirs.append(None)  # the system default (/usr/share/locale)
    return dirs


def _load() -> gettext.NullTranslations:
    for localedir in _locale_dirs():
        if gettext.find(DOMAIN, localedir):
            return gettext.translation(DOMAIN, localedir=localedir, fallback=True)
    return gettext.NullTranslations()


_translation = _load()


def _(message: str) -> str:
    return _translation.gettext(message)


def ngettext(singular: str, plural: str, n: int) -> str:
    return _translation.ngettext(singular, plural, n)


def reload() -> None:
    """Re-read the translation (tests switch languages with this)."""
    global _translation
    _translation = _load()


def install_qt_translations(app) -> None:
    """Translate Qt's own texts too (file dialogs, standard buttons)."""
    from PySide6.QtCore import QLibraryInfo, QLocale, QTranslator

    translator = QTranslator(app)
    path = QLibraryInfo.path(QLibraryInfo.TranslationsPath)
    if translator.load(QLocale(), "qtbase", "_", path):
        app.installTranslator(translator)
        app._qt_translator = translator  # keep it alive
