#!/usr/bin/env bash
# Run Pomodoro Pet from this checkout without packaging it:
# adds a launcher entry + icon under ~/.local/share and a `pomodoro-pet`
# wrapper in ~/.local/bin. Use --uninstall to remove them.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
DATA=${XDG_DATA_HOME:-$HOME/.local/share}
BIN=$HOME/.local/bin

if [[ "${1:-}" == "--uninstall" ]]; then
    rm -f "$BIN/pomodoro-pet" "$DATA/applications/pomodoro-pet.desktop" "$DATA/kglobalaccel/pomodoro-pet.desktop"
    for s in 48 64 128 256; do rm -f "$DATA/icons/hicolor/${s}x${s}/apps/pomodoro-pet.png"; done
    echo "Removed."
    exit 0
fi

mkdir -p "$BIN" "$DATA/applications" "$DATA/kglobalaccel"
cat >"$BIN/pomodoro-pet" <<WRAP
#!/bin/sh
export PYTHONPATH="$ROOT\${PYTHONPATH:+:\$PYTHONPATH}"
exec python3 -m pomodoro_pet "\$@"
WRAP
chmod +x "$BIN/pomodoro-pet"
sed "s|^Exec=pomodoro-pet|Exec=$BIN/pomodoro-pet|" "$ROOT/packaging/pomodoro-pet.desktop" >"$DATA/applications/pomodoro-pet.desktop"
# Global shortcuts (Meta+Shift+P = start/pause); KDE picks these up at next login.
cp "$DATA/applications/pomodoro-pet.desktop" "$DATA/kglobalaccel/pomodoro-pet.desktop"
for s in 48 64 128 256; do
    install -Dm644 "$ROOT/assets/icons/pomodoro-pet-$s.png" "$DATA/icons/hicolor/${s}x${s}/apps/pomodoro-pet.png"
done
command -v update-desktop-database >/dev/null && update-desktop-database "$DATA/applications" || true
command -v kbuildsycoca6 >/dev/null && kbuildsycoca6 >/dev/null 2>&1 || true
echo "Installed. Look for \"Pomodoro Pet\" in the app launcher, or run: $BIN/pomodoro-pet"
