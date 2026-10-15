"""Entry point for the frozen macOS app (PyInstaller needs a script)."""

from pomodoro_pet.app import main

raise SystemExit(main())
