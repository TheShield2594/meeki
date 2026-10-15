#!/bin/sh
# Install everything except the Python package itself into a staging root.
# Shared by packaging/arch/PKGBUILD and packaging/aur/PKGBUILD:
#   sh packaging/install-files.sh "$pkgdir"
# Run from the source root after `python tools/i18n.py compile`.
set -eu
root=${1:?usage: install-files.sh DESTDIR}
name=pomodoro-pet

install -d "$root/usr/share/$name"
cp -r assets "$root/usr/share/$name/"
install -Dm644 packaging/$name.desktop "$root/usr/share/applications/$name.desktop"
# the same file in kglobalaccel/ registers the KDE global shortcuts (Meta+Shift+P etc.)
install -d "$root/usr/share/kglobalaccel"
ln -sf "../applications/$name.desktop" "$root/usr/share/kglobalaccel/$name.desktop"
for s in 48 64 128 256; do
  install -Dm644 "assets/icons/$name-$s.png" "$root/usr/share/icons/hicolor/${s}x${s}/apps/$name.png"
done
install -Dm644 packaging/kwin/$name.kwinrule "$root/usr/share/$name/kwin/$name.kwinrule"
install -Dm755 packaging/kwin/install-kwin-rule.sh "$root/usr/share/$name/kwin/install-kwin-rule.sh"
install -Dm644 LICENSE "$root/usr/share/licenses/$name/LICENSE"
# translations (compiled into build/locale by tools/i18n.py)
if [ -d build/locale ]; then
  for mo in build/locale/*/LC_MESSAGES/$name.mo; do
    [ -e "$mo" ] || continue
    lang=$(basename "$(dirname "$(dirname "$mo")")")
    install -Dm644 "$mo" "$root/usr/share/locale/$lang/LC_MESSAGES/$name.mo"
  done
fi
