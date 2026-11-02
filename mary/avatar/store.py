"""Where a persona's face lives, whether it is on, and how it is erased or carried: user/personas/<persona>/face/.

    manifest.json   what the prepared images are (poses, patches and where each goes) and which photo they were made from
                    (ids only: no name, no path)
    state.json      {"enabled": true|false}: the one-click switch between the face and the HUD's plain animation
    *.webp          the prepared images

The face is private: it stays in the persona's own folder, a character card carries it only when the person says so, and a face
that arrives in a card is validated (names, sizes, count) and arrives switched off until the person has looked at its preview.
"""
from __future__ import annotations

import io
import json
import logging
import re
import shutil
import zipfile
from pathlib import Path

from mary.config import user_dir

log = logging.getLogger(__name__)

MANIFEST = "manifest.json"
STATE = "state.json"
VERSION = 1
SHAPES = "ABCDEFGH"                  # the mouth shapes that are patches; X (rest) is the pose's own base image
EYE_STATES = ("half", "closed")
MAX_FILE = 2_000_000                 # bytes in one image
MAX_TOTAL = 24_000_000               # bytes in all of them
MAX_FILES = 200
MAX_SIDE = 4096                      # pixels, either way
_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]{0,63}$")
_IMAGE = re.compile(r"^[a-z0-9_]{1,60}\.webp$")


class FaceError(ValueError):
    """Something about a face that can't be done; the message is a sentence for the person."""


def _key(key: str) -> str:
    if not isinstance(key, str) or not _KEY.match(key):
        raise FaceError("That isn't a persona name.")
    return key


def folder(key: str) -> Path:
    return user_dir() / "personas" / _key(key) / "face"


def _images(m: dict):
    """Every (file, rect) a manifest names."""
    for pose in m.get("poses") or []:
        yield pose.get("base"), None
        for group in ("mouth", "eyes", "brows"):
            for entry in (pose.get(group) or {}).values():
                yield (entry or {}).get("file"), (entry or {}).get("rect")


def validate(m, root: Path | None = None) -> dict:
    """The manifest if it is well-formed (and, with `root`, its images are there and the right size); else FaceError."""
    if not isinstance(m, dict) or m.get("version") != VERSION:
        raise FaceError("That face was made by a different version of MARY.")
    size = m.get("size")
    if not (isinstance(size, list) and len(size) == 2 and all(isinstance(v, int) and 16 <= v <= MAX_SIDE for v in size)):
        raise FaceError("That face has no usable size.")
    poses = m.get("poses")
    if not (isinstance(poses, list) and 1 <= len(poses) <= 12):
        raise FaceError("That face has no poses.")
    for pose in poses:
        if not isinstance(pose, dict) or not _IMAGE.match(str(pose.get("base") or "")):
            raise FaceError("A pose of that face has no picture.")
        if set(pose.get("mouth") or {}) - set(SHAPES) or set(pose.get("eyes") or {}) - set(EYE_STATES):
            raise FaceError("A pose of that face has parts MARY doesn't know.")
    for name, rect in _images(m):
        if not _IMAGE.match(str(name or "")):
            raise FaceError("That face names a file it can't have.")
        if rect is not None and not (isinstance(rect, list) and len(rect) == 4 and all(isinstance(v, int) for v in rect)
                                     and rect[2] > 0 and rect[3] > 0 and rect[0] >= 0 and rect[1] >= 0
                                     and rect[0] + rect[2] <= size[0] and rect[1] + rect[3] <= size[1]):
            raise FaceError("A part of that face sits outside the picture.")
        if root is not None:
            if not (root / name).is_file():
                raise FaceError("A picture of that face is missing.")
            _check_dimensions(root / name)
    return m


def _check_dimensions(path: Path) -> None:
    from PIL import Image

    try:
        with Image.open(path) as im:       # the header only: nothing is decoded
            w, h = im.size
    except Exception:
        raise FaceError("A picture of that face can't be read.") from None
    if not (0 < w <= MAX_SIDE and 0 < h <= MAX_SIDE):
        raise FaceError("A picture of that face is too big.")


def manifest(key: str) -> dict | None:
    """The persona's prepared face, or None (no face, or one that can't be used)."""
    root = folder(key)
    try:
        return validate(json.loads((root / MANIFEST).read_text(encoding="utf-8")), root)
    except (OSError, ValueError):
        return None


def signature(key: str) -> tuple:
    """Changes whenever the face is made, replaced, switched or erased, by anything (the settings page, a persona
    card, deleting the person): what the HUD watches to show the change at once."""
    root = folder(key)
    out = []
    for name in (MANIFEST, STATE):
        try:
            st = (root / name).stat()
            out.append((st.st_mtime_ns, st.st_size))
        except OSError:
            out.append(None)
    return tuple(out)


def available(key: str) -> bool:
    return manifest(key) is not None


def enabled(key: str) -> bool:
    """Is the face what the HUD shows for this persona (it exists and the person switched it on)?"""
    if not available(key):
        return False
    try:
        return bool(json.loads((folder(key) / STATE).read_text(encoding="utf-8")).get("enabled"))
    except (OSError, ValueError):
        return False


def set_enabled(key: str, on: bool) -> bool:
    """The one-click switch. A face that isn't there can't be switched on."""
    if on and not available(key):
        raise FaceError("There is no face to switch on yet.")
    root = folder(key)
    if root.exists():
        (root / STATE).write_text(json.dumps({"enabled": bool(on)}), encoding="utf-8")
    return bool(on)


def erase(key: str) -> bool:
    """Take the face away for good: every image and the switch. Returns whether there was one."""
    root = folder(key)
    existed = root.exists()
    shutil.rmtree(root, ignore_errors=True)
    if existed:
        log.info("a persona face was erased")
    return existed


def made_from(person_ids) -> list[str]:
    """The personas whose face was made from one of these people (None: from anyone). The manifest records the person's id, not
    their name."""
    out = []
    for m in (user_dir() / "personas").glob("*/face/" + MANIFEST):
        try:
            who = (json.loads(m.read_text(encoding="utf-8")).get("source") or {}).get("person")
        except (OSError, ValueError):
            continue
        if who is not None and (person_ids is None or who in {int(p) for p in person_ids}):
            out.append(m.parent.parent.name)
    return out


def install(key: str, writer) -> dict:
    """Make a face: `writer(tmp_folder)` writes manifest.json and the images; they replace any face there was, switched OFF
    (the person looks at the preview and chooses). Nothing changes if it fails."""
    root = folder(key)
    tmp = root.with_name("face.tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    try:
        writer(tmp)
        m = validate(json.loads((tmp / MANIFEST).read_text(encoding="utf-8")), tmp)
        (tmp / STATE).write_text(json.dumps({"enabled": False}), encoding="utf-8")
        shutil.rmtree(root, ignore_errors=True)
        tmp.replace(root)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return m


# ------------------------------------------------------------------ carried in a character card (only when the person says so)


def pack(key: str) -> bytes:
    """The face as one zip (manifest and images), to travel in a card."""
    m = manifest(key)
    if m is None:
        raise FaceError("There is no face to export.")
    root, out = folder(key), io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as z:
        z.write(root / MANIFEST, MANIFEST)
        for name in sorted({n for n, _ in _images(m)}):
            z.write(root / name, name)
    return out.getvalue()


def unpack(key: str, data: bytes) -> dict:
    """A face from a card: checked file by file (names, sizes, count), then installed, switched off."""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise FaceError("That face isn't readable.") from None
    infos = z.infolist()
    if not infos or len(infos) > MAX_FILES or sum(i.file_size for i in infos) > MAX_TOTAL:
        raise FaceError("That face is too big or empty.")
    for i in infos:
        if i.filename != MANIFEST and not _IMAGE.match(i.filename):
            raise FaceError("That face holds a file it shouldn't.")
        if i.file_size > MAX_FILE:
            raise FaceError("That face holds a picture that is too big.")

    def write(tmp: Path) -> None:
        for i in infos:
            (tmp / i.filename).write_bytes(z.read(i))

    return install(key, write)
