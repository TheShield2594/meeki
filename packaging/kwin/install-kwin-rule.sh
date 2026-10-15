#!/usr/bin/env bash
# Install (or with --uninstall, remove) the Pomodoro Pet KWin window rule.
# Keeps the pet above other windows, out of the taskbar/Alt+Tab, and makes
# KWin remember its position. Works on Plasma 6 (and 5).
set -euo pipefail

GROUP=pomodoro-pet
FILE=kwinrulesrc
KW=$(command -v kwriteconfig6 || command -v kwriteconfig5 || true)
KR=$(command -v kreadconfig6 || command -v kreadconfig5 || true)
if [[ -z "$KW" || -z "$KR" ]]; then
    echo "kwriteconfig6/kreadconfig6 not found (package: kconfig)." >&2
    exit 1
fi

rules=$("$KR" --file "$FILE" --group General --key rules --default "")
IFS=, read -r -a list <<<"$rules"
filtered=()
for r in "${list[@]}"; do [[ -n "$r" && "$r" != "$GROUP" ]] && filtered+=("$r"); done

if [[ "${1:-}" == "--uninstall" ]]; then
    "$KW" --file "$FILE" --group "$GROUP" --key Description --delete
    for key in wmclass wmclasscomplete wmclassmatch title titlematch above aboverule skiptaskbar \
               skiptaskbarrule skippager skippagerrule skipswitcher skipswitcherrule positionrule; do
        "$KW" --file "$FILE" --group "$GROUP" --key "$key" --delete
    done
else
    w() { "$KW" --file "$FILE" --group "$GROUP" --key "$1" "$2"; }
    w Description "Pomodoro Pet (keep above, no taskbar, remember position)"
    w wmclass pomodoro-pet
    w wmclasscomplete false
    w wmclassmatch 1
    w title "Pomodoro Pet"
    w titlematch 1
    w above true
    w aboverule 2
    w skiptaskbar true
    w skiptaskbarrule 2
    w skippager true
    w skippagerrule 2
    w skipswitcher true
    w skipswitcherrule 2
    w positionrule 4
    filtered+=("$GROUP")
fi

joined=$(IFS=,; echo "${filtered[*]}")
"$KW" --file "$FILE" --group General --key rules "$joined"
"$KW" --file "$FILE" --group General --key count "${#filtered[@]}"

# Ask KWin to reload its rules.
if command -v qdbus6 >/dev/null; then
    qdbus6 org.kde.KWin /KWin reconfigure >/dev/null || true
else
    dbus-send --session --type=method_call --dest=org.kde.KWin /KWin org.kde.KWin.reconfigure || true
fi
echo "Done${1:+ ($1)}."
