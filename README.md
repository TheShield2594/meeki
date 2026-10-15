# Pomodoro Pet 🐶🍅

A tiny desktop pet for **KDE Plasma** (Wayland first) that runs your Pomodoro
timer. The pet is a fluffy **Bichon Frise × Biewer Terrier** pup: white Bichon
fluff, Biewer tricolour (blue-black saddle, gold ears and brows) and a top-knot
with a pink bow.

![The pup in its four states](docs/preview.png)

- Frameless, transparent, always-on-top window with an animated pup
- Drag it anywhere. Its position is remembered between launches
- Click the pup to set focus / short break / long break / rounds
  (defaults **25 / 5 / 15 / 4**) and to Start / Pause / Skip / Reset
- Speech bubble with the remaining time, colour-coded by phase, plus
  round-progress dots
- Four animation states: **idle**, **focusing** (glasses + laptop),
  **break** (napping), **celebrating** (after each focus round and at the end
  of the session)
- KDE notification and a soft chime when a phase ends (both optional)
- **Do Not Disturb while focusing:** Plasma silences other notifications during
  focus rounds and lets them through again on breaks
- **Global shortcut** <kbd>Meta</kbd>+<kbd>Shift</kbd>+<kbd>P</kbd> to start
  or pause, plus a command-line remote (`pomodoro-pet --toggle`, `--status`, …)
- **Sleep-proof:** time keeps counting while the laptop is suspended. If a
  phase ended while you were away, the pup waits for you instead of silently
  running the next one
- **Resumes after restarts and crashes:** the current session is saved and
  picked up on the next launch
- The little **×** quits completely. No tray icon, no background process
- Settings live in `~/.config/pomodoro-pet/config.json`

Built with Python 3 + PySide6 (Qt 6).

---

## Run from source

```bash
sudo pacman -S --needed python pyside6 libnotify   # CachyOS / Arch
git clone https://github.com/TheShield2594/meeki.git pomodoro-pet
cd pomodoro-pet
python -m pomodoro_pet
```

Options:

```
python -m pomodoro_pet --toggle           # start/pause (launches the pet if needed)
python -m pomodoro_pet --status           # e.g. "Focus · round 2/4 · 12:34 left"
python -m pomodoro_pet --backend xcb      # force XWayland for this run
python -m pomodoro_pet --reset-position   # forget the saved position
python -m pomodoro_pet -v                 # verbose logging
```

To get a launcher entry without packaging (runs from the checkout):

```bash
packaging/install-user.sh          # adds ~/.local/bin/pomodoro-pet + menu entry + icon
packaging/install-user.sh --uninstall
```

With a virtualenv instead of the system PySide6:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e .
pomodoro-pet
```

## Install as an Arch package

The PKGBUILD builds from this checkout:

```bash
cd packaging/arch
makepkg -si
```

This installs:

| Path | What |
|---|---|
| `/usr/bin/pomodoro-pet` | launcher |
| `/usr/lib/python3.x/site-packages/pomodoro_pet/` | the app |
| `/usr/share/pomodoro-pet/assets/` | sprites, sound, icon |
| `/usr/share/applications/pomodoro-pet.desktop` | KDE app-launcher entry |
| `/usr/share/kglobalaccel/pomodoro-pet.desktop` | global shortcuts (symlink) |
| `/usr/share/icons/hicolor/*/apps/pomodoro-pet.png` | icon |
| `/usr/share/pomodoro-pet/kwin/` | optional KWin window rule + installer |

Optional dependencies: `libnotify` (notifications), `pipewire` or `libpulse`
(sound), `xorg-xwayland` (XWayland fallback), `kconfig` (rule installer).

## Using it

| Action | How |
|---|---|
| Open the controls | click the pup or the bubble |
| Move the pup | drag it |
| Start / pause from anywhere | <kbd>Meta</kbd>+<kbd>Shift</kbd>+<kbd>P</kbd> |
| Quit | the small **×** at the top-left (shows clearly on hover) |

While the popover is open you can change durations. Changes to a running phase
take effect from the next phase. An idle timer updates immediately. With
`auto_advance` on (the default), breaks and focus rounds follow each other
automatically. After the long break the session is complete, and the pup
celebrates.

The bubble dims and shows a pause sign while the timer is paused, and the pup
freezes mid-pose.

### Keyboard shortcuts and remote control

The launcher entry has three actions: *Start / Pause Timer*, *Skip Current
Phase* and *Reset Session*. Both install methods also register them as KDE
global shortcuts. Start/Pause defaults to <kbd>Meta</kbd>+<kbd>Shift</kbd>+<kbd>P</kbd>.
The other two have no default, so you can bind them in *System Settings →
Keyboard → Shortcuts → Pomodoro Pet*. New shortcut files are picked up at your
next login.

The shortcuts run the same commands you can use in a terminal or script:

| Command | Effect |
|---|---|
| `pomodoro-pet --toggle` | start or pause. Launches the pet and starts if it isn't running |
| `pomodoro-pet --start` / `--pause` | start/resume, or pause |
| `pomodoro-pet --skip` | end the current phase now |
| `pomodoro-pet --reset` | back to round 1, idle |
| `pomodoro-pet --show` | bring the pet back (e.g. from tray mode) |
| `pomodoro-pet --status` | print the phase and time left (exit code 1 if not running) |

Launching `pomodoro-pet` again while it's running doesn't create a second pet.
Under the hood the commands are D-Bus calls (`io.github.pomodoropet`,
`/PomodoroPet`, methods `Toggle`, `Start`, `Pause`, `Skip`, `Reset`, `Show`,
`Status`), so anything that can call D-Bus can drive the timer.

### Do Not Disturb

While a focus round is running, Pomodoro Pet asks Plasma to hold back other
notifications, the same as the *Do not disturb* switch in the notification
applet. Pausing, breaks and quitting end it, and Plasma also ends it if the app
crashes, so it can't get stuck on. Turn it off with the checkbox in the popover
or `"dnd_during_focus": false`.

### Suspend, restarts and crashes

- The timer counts real elapsed time, including while the machine is
  suspended.
- If a phase ended while you were away (lid closed, app not running), you get
  a *"Welcome back"* notification, and the timer waits paused at the start of
  the next phase. Press start when you're ready.
- The current session (phase, round, end time) is written to
  `~/.local/state/pomodoro-pet/session.json` at every change and on quit. The
  next launch picks it up. Sessions older than 12 hours are dropped.

## Configuration

`~/.config/pomodoro-pet/config.json` (or `$XDG_CONFIG_HOME/pomodoro-pet/`) is
written automatically. Keys:

| Key | Default | Meaning |
|---|---|---|
| `focus_minutes` | `25` | focus length (1–240) |
| `short_break_minutes` | `5` | short break (1–120) |
| `long_break_minutes` | `15` | long break (1–240) |
| `rounds` | `4` | focus rounds before the long break (1–12) |
| `auto_advance` | `true` | start the next phase automatically |
| `notifications` | `true` | KDE notification when a phase ends |
| `dnd_during_focus` | `true` | Plasma Do Not Disturb during focus rounds |
| `sound` | `true` | play a chime when a phase ends |
| `sound_file` | `null` | custom `.wav`/`.ogg` instead of the bundled chime |
| `position` | `null` | last window position (`{"x":…, "y":…}`), managed for you |
| `scale` | `1.0` | pet size multiplier (0.5–4) |
| `sprite_dir` | `null` | folder with your own sprites (see below) |
| `backend` | `"auto"` | `"auto"`, `"wayland"` or `"xcb"` (see Wayland notes) |
| `kwin_integration` | `true` | use KWin scripting for keep-above/positioning on Wayland |
| `minimize_to_tray` | `false` | **experimental stub**, see below |

Edit it while the app is closed. The app rewrites it on quit.

## Custom sprites

Sprites are looked up per state (`idle`, `focusing`, `break`, `celebrating`)
in `sprite_dir` first, then the bundled `assets/sprites/`. Each state can be:

- a folder of frames: `<state>/00.png`, `<state>/01.png`, … (sorted by name), or
- a horizontal sprite sheet `<state>.png` made of square frames.

Frames are drawn at about 112 logical px, so 224–256 px PNGs look crisp on
HiDPI. Missing states fall back to the bundled art. The bundled placeholder art
and the chime are generated by `tools/generate_assets.py`. Tweak and re-run it,
or drop real art over the PNGs.

## Wayland notes and limitations

Plain Wayland deliberately gives apps **no way to choose their own position,
read it back, or stay above other windows**. Qt silently ignores `move()` and
`WindowStaysOnTopHint` there. Pomodoro Pet handles this as follows:

1. **KDE Plasma Wayland (default, native).** On start the app loads a small
   **KWin script** over D-Bus (`org.kde.KWin /Scripting`). The script finds the
   pet window by app-id + title and:
   - sets *keep above* and hides it from the taskbar, pager and Alt+Tab,
   - moves it to the saved position,
   - reports every position change back to the app over D-Bus, so the position
     can be saved.

   The script is unloaded when you quit. Dragging uses
   `QWindow.startSystemMove()`, the Wayland-native way to move a window, so it
   works without any of the above.
2. **Other Wayland compositors** (GNOME, Sway, …) with `backend: "auto"`: the app
   starts under **XWayland** (`QT_QPA_PLATFORM=xcb`), where positioning and
   always-on-top work as on X11. Needs `xorg-xwayland`.
3. **X11 sessions:** everything works natively.

If the KWin script can't load (unusual Plasma build, scripting disabled), the
app keeps running and logs a hint. Then pick one of these fallbacks:

- **KWin window rule.** Import `packaging/kwin/pomodoro-pet.kwinrule` in
  *System Settings → Window Management → Window Rules → Import…*, or run
  `packaging/kwin/install-kwin-rule.sh` (installed copy:
  `/usr/share/pomodoro-pet/kwin/`). It forces keep-above and no-taskbar, and
  has KWin remember the position. You can also set `"kwin_integration": false`
  and rely on the rule only.
- **XWayland.** Set `"backend": "xcb"` in the config, or run with
  `--backend xcb`. This is the most predictable option. The downside is
  slightly blurry rendering with fractional scaling.

Other known quirks:

- On Wayland the pet may appear for a split second at the compositor's default
  spot before KWin moves it to its saved position.
- The KWin script's position reports, the remote control and the shortcuts
  all use the `io.github.pomodoropet` D-Bus name. A lock file keeps it to one
  instance.
- An explicit `QT_QPA_PLATFORM` in your environment always wins over `backend`.
- Transparent areas around the pup let clicks through (input mask), so the
  window doesn't block what's underneath.

## "Minimize to tray" (experimental stub)

Off by default. Set `"minimize_to_tray": true` and the popover gets a
*Minimize to tray* button. It hides the pup and shows the live minutes in a
system-tray icon (tooltip shows the full countdown). Clicking the tray icon
brings the pup back. Not done yet: tray context menu, persisting tray mode,
nicer icon. See `pomodoro_pet/tray.py`.

## Development

```bash
pip install pytest ruff
QT_QPA_PLATFORM=offscreen python -m pytest
ruff check .
python tools/generate_assets.py   # regenerate placeholder sprites/icon/chime
```

Layout:

```
pomodoro_pet/
  app.py        entry point, wiring, lifecycle
  timer.py      Pomodoro state machine (suspend-aware clock, away detection, save/restore)
  pet.py        the frameless pet window: painting, animation, drag/click
  popover.py    settings + Start/Pause/Skip/Reset popover
  wayland.py    backend selection + KWin scripting integration
  remote.py     D-Bus service + client behind --toggle/--status and the shortcuts
  dnd.py        Plasma Do Not Disturb during focus rounds
  notify.py     notify-send / gdbus / kdialog + pw-play / paplay / aplay
  assets.py     asset lookup and sprite loading (folders or sheets)
  config.py     ~/.config/pomodoro-pet/config.json
  tray.py       experimental tray stub
assets/         sprites, icon, chime
packaging/      .desktop, PKGBUILD, KWin rule, user install script
tools/          asset generator
tests/          unit tests + an end-to-end D-Bus test (private bus, fake notification server)
```

## License

MIT
