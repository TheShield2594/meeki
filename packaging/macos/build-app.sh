#!/bin/sh
# Build "Pomodoro Pet.app" and a .dmg on macOS, from the source root or anywhere:
#   python3 -m pip install PySide6-Essentials pyinstaller pillow
#   sh packaging/macos/build-app.sh
# Output: dist/Pomodoro Pet.app and dist/Pomodoro-Pet-<version>-macos-<arch>.dmg
set -eu
cd "$(dirname "$0")/../.."
python=${PYTHON:-python3}

"$python" tools/i18n.py compile
"$python" -m PyInstaller --noconfirm --clean --distpath dist --workpath build/pyinstaller \
  packaging/macos/pomodoro-pet.spec

app="dist/Pomodoro Pet.app"
version=$("$python" -c 'import sys; sys.path.insert(0, "tools"); import i18n; print(i18n.version())')
dmg="dist/Pomodoro-Pet-$version-macos-$(uname -m).dmg"

stage=build/dmg
rm -rf "$stage" "$dmg"
mkdir -p "$stage"
cp -R "$app" "$stage/"
ln -s /Applications "$stage/Applications"
hdiutil create -volname "Pomodoro Pet" -srcfolder "$stage" -ov -format UDZO "$dmg"
echo "Built $app and $dmg"
