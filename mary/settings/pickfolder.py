"""python -m mary.settings.pickfolder: a native "choose a folder" window; prints the folder's path (nothing if cancelled).
The settings page runs it in its own process, because a window can't be opened from the page's server threads."""
from __future__ import annotations

import sys


def main() -> None:
    import tkinter
    from tkinter import filedialog

    root = tkinter.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    path = filedialog.askdirectory(title="Choose a folder of photos", mustexist=True)
    root.destroy()
    sys.stdout.reconfigure(encoding="utf-8")
    print(path or "")


if __name__ == "__main__":
    main()
