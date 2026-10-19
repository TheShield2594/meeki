# PyInstaller spec for "Pomodoro Pet.app". Build with packaging/macos/build-app.sh.
import os
import re

ROOT = os.path.abspath(os.path.join(SPECPATH, "..", ".."))
with open(os.path.join(ROOT, "pyproject.toml"), encoding="utf-8") as fh:
    VERSION = re.search(r'^version = "([^"]+)"', fh.read(), re.M).group(1)

# Bundled next to the code, where assets.py and i18n.py look in a source checkout.
datas = [(os.path.join(ROOT, "assets"), "assets")]
if os.path.isdir(os.path.join(ROOT, "build", "locale")):
    datas.append((os.path.join(ROOT, "build", "locale"), os.path.join("build", "locale")))

a = Analysis(
    [os.path.join(SPECPATH, "launcher.py")],
    pathex=[ROOT],
    datas=datas,
    excludes=["tkinter", "unittest", "pydoc", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtWebEngineCore"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="pomodoro-pet",
    console=False,
    argv_emulation=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="pomodoro-pet")
app = BUNDLE(
    coll,
    name="Pomodoro Pet.app",
    icon=os.path.join(ROOT, "assets", "icons", "pomodoro-pet.png"),  # converted to .icns (needs Pillow)
    bundle_identifier="io.github.pomodoropet",
    version=VERSION,
    info_plist={
        "CFBundleName": "Pomodoro Pet",
        "CFBundleDisplayName": "Pomodoro Pet",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "LSMinimumSystemVersion": "12.0",
        "LSUIElement": True,  # a desktop pet: no Dock icon or menu bar
        "NSHighResolutionCapable": True,
        "NSRequiresAquaSystemAppearance": False,  # follow dark mode
        "NSHumanReadableCopyright": "MIT License",
    },
)
