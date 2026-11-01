"""The folders you give MARY, and finding the picture files in them. Nothing is read here except directory listings.

Left alone, and counted so the page can say so:
  - OneDrive "files on demand" that are online only: reading one would download it, so they are listed, never opened
  - hidden and system folders, thumbnail caches, MARY's own folders (her screenshots, her memory)
  - links and junctions (no loops, and no wandering out of the folder you chose)
"""
from __future__ import annotations

import os
import stat as stat_mod
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from mary.photos.read import EXTENSIONS

# Windows file attributes
HIDDEN = 0x2
SYSTEM = 0x4
OFFLINE = 0x1000
RECALL_ON_OPEN = 0x40000
RECALL_ON_DATA_ACCESS = 0x400000
ONLINE_ONLY = OFFLINE | RECALL_ON_OPEN | RECALL_ON_DATA_ACCESS

SKIP_DIRS = {"$recycle.bin", "system volume information", "@eadir", ".thumbnails", ".thumbs", "thumbnails", "__macosx",
             ".git", "node_modules", "appdata", ".trash", ".stfolder", ".tmp.drivedownload"}


@dataclass(frozen=True)
class Found:
    path: str
    size: int
    mtime: float
    online_only: bool


def is_picture(name: str) -> bool:
    return Path(name).suffix.lower() in EXTENSIONS


def hidden(attrs: int) -> bool:
    return bool(attrs & (HIDDEN | SYSTEM))


def walk(root: Path, recursive: bool = True, skip: tuple[Path, ...] = ()) -> Iterator[Found]:
    """Every picture file under `root` (a listing: nothing is opened). Errors on one folder skip that folder."""
    skip_paths = {os.path.normcase(str(p)) for p in skip}
    stack = [Path(root)]
    while stack:
        folder = stack.pop()
        if os.path.normcase(str(folder)) in skip_paths:
            continue
        try:
            entries = list(os.scandir(folder))
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_symlink() or (hasattr(entry, "is_junction") and entry.is_junction()):
                    continue
                st = entry.stat(follow_symlinks=False)
                attrs = getattr(st, "st_file_attributes", 0)
                if entry.is_dir(follow_symlinks=False):
                    if recursive and not entry.name.startswith(".") and entry.name.lower() not in SKIP_DIRS \
                            and not hidden(attrs):
                        stack.append(Path(entry.path))
                elif stat_mod.S_ISREG(st.st_mode) and is_picture(entry.name) and not hidden(attrs):
                    yield Found(entry.path, st.st_size, st.st_mtime, bool(attrs & ONLINE_ONLY))
            except OSError:
                continue


def suggestions(known: list[Path]) -> list[dict]:
    """Folders people keep photos in that exist on this PC and aren't added yet (one click to add). Nothing in them is
    looked at until one is added."""
    from mary.winutil import known_folder

    have = {os.path.normcase(str(k)) for k in known}
    pictures = known_folder("Pictures")
    candidates = [("Pictures", pictures), ("Camera Roll", pictures / "Camera Roll"),
                  ("Saved Pictures", pictures / "Saved Pictures")]
    if os.environ.get("OneDrive"):
        candidates.append(("OneDrive Pictures", Path(os.environ["OneDrive"]) / "Pictures"))
    out = []
    for label, path in candidates:
        key = os.path.normcase(str(path))
        try:
            if path.is_dir() and key not in have:
                have.add(key)      # Pictures and OneDrive's Pictures can be one folder
                out.append({"label": label, "path": str(path)})
        except OSError:
            continue
    return out
