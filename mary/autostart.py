"""Start MARY when you sign in: a shortcut in your Startup folder that runs her in the background.

    python -m mary.autostart enable    # add the shortcut
    python -m mary.autostart disable   # remove it
    python -m mary.autostart status

The shortcut runs `pythonw -m mary --background`: no console window, a tray icon instead. It's an
ordinary shortcut, so you can also see or delete it in Task Manager › Startup apps.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from mary.config import PROJECT_DIR

SHORTCUT = "MARY.lnk"


def startup_folder() -> Path:
    return Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def _pythonw() -> Path:
    venv = PROJECT_DIR / ".venv" / "Scripts" / "pythonw.exe"
    return venv if venv.exists() else Path(sys.executable).with_name("pythonw.exe")


def is_enabled(folder: Path | None = None) -> bool:
    return ((folder or startup_folder()) / SHORTCUT).exists()


def enable(folder: Path | None = None) -> Path:
    import win32com.client

    path = (folder or startup_folder()) / SHORTCUT
    link = win32com.client.Dispatch("WScript.Shell").CreateShortCut(str(path))
    link.TargetPath = str(_pythonw())
    link.Arguments = "-m mary --background"
    link.WorkingDirectory = str(PROJECT_DIR)
    link.Description = "MARY, your voice assistant (runs in the background; tray icon)"
    link.WindowStyle = 7  # minimised; pythonw shows no window anyway
    link.save()
    return path


def disable(folder: Path | None = None) -> bool:
    path = (folder or startup_folder()) / SHORTCUT
    if path.exists():
        path.unlink()
        return True
    return False


def main() -> None:
    action = sys.argv[1] if len(sys.argv) > 1 else "status"
    if action == "enable":
        print(f"MARY will start when you sign in (shortcut: {enable()}).")
    elif action == "disable":
        print("Removed: MARY won't start at sign-in." if disable() else "MARY wasn't set to start at sign-in.")
    elif action == "status":
        print("MARY starts when you sign in." if is_enabled() else
              "MARY doesn't start at sign-in. Turn it on with: python -m mary.autostart enable")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
