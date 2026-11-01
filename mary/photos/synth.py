"""Invented pictures with known answers, for the tests and the benchmark. Never a real photo, and nothing here reads one.

`picture(seed)` makes a picture that looks like nothing but is different from every other seed's (its difference hash is
effectively random), so a test can say "these two are the same photo" or "these two are not" and be right.
`hard_set(root)` writes a small library of the awkward cases (no dates, scans, the same person's print photographed today,
copies in every form, bursts that must not merge, tiny and broken files) and returns what the answer is for each.
`bulk(root, n)` writes n realistic files for the benchmark.
"""
from __future__ import annotations

import io
import os
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFont
from PIL.TiffImagePlugin import IFDRational

# Places for the tests' tiny gazetteer: invented names, real positions on the map.
SITES = {"harbourton": (5.1053, -1.2466), "northfield": (51.5072, -0.1276), "kestrel bay": (-33.9249, 18.4241)}


def picture(seed: int, size: tuple[int, int] = (480, 320)) -> Image.Image:
    """A smooth random landscape of light and colour with a few shapes on it: distinct for every seed."""
    rng = random.Random(seed)
    w, h = size
    field = np.array([[rng.random() for _ in range(12)] for _ in range(9)], dtype=np.float32)
    base = Image.fromarray((field * 255).astype(np.uint8)).resize((w, h), Image.Resampling.BICUBIC)
    tint = tuple(rng.randrange(40, 255) for _ in range(3))
    img = Image.merge("RGB", [base.point(lambda v, t=t: int(v * t / 255)) for t in tint])
    draw = ImageDraw.Draw(img)
    for _ in range(rng.randrange(5, 9)):
        x, y = rng.randrange(w), rng.randrange(h)
        r = rng.randrange(min(w, h) // 8, min(w, h) // 3)
        colour = tuple(rng.randrange(0, 256) for _ in range(3))
        box = (x - r, y - r, x + r, y + r)
        draw.ellipse(box, fill=colour) if rng.random() < 0.5 else draw.rectangle(box, fill=colour)
    return img


def near(img: Image.Image, seed: int = 0) -> Image.Image:
    """Almost the same picture (the next frame of a burst): a few pixels of difference, no more."""
    out = img.copy()
    rng = random.Random(seed)
    draw = ImageDraw.Draw(out)
    x, y = rng.randrange(out.width), rng.randrange(out.height)
    draw.ellipse((x, y, x + 6, y + 6), fill=(255, 255, 255))
    return out


def with_sign(img: Image.Image, text: str) -> Image.Image:
    """A white board with dark lettering across the middle (for the text-reading tests)."""
    out = img.copy()
    draw = ImageDraw.Draw(out)
    h = out.height
    draw.rectangle((out.width // 10, h * 4 // 10, out.width * 9 // 10, h * 6 // 10), fill=(255, 255, 255))
    try:
        font = ImageFont.truetype("arial.ttf", max(14, h // 8))
    except OSError:
        font = ImageFont.load_default()
    draw.text((out.width // 8, h * 4 // 10 + h // 40), text, fill=(0, 0, 0), font=font)
    return out


def as_scan(img: Image.Image) -> Image.Image:
    """What a flatbed scan of an old print looks like: faded, warm, with a white border and a little dust."""
    out = ImageEnhance.Color(img).enhance(0.35)
    out = ImageEnhance.Contrast(out).enhance(0.8)
    r, g, b = out.split()
    out = Image.merge("RGB", (r.point(lambda v: min(255, v + 18)), g, b.point(lambda v: max(0, v - 14))))
    framed = Image.new("RGB", (out.width + 40, out.height + 40), (246, 244, 238))
    framed.paste(out, (20, 20))
    return framed


# ---------------------------------------------------------------- writing files


def _exif(taken: datetime | None, gps: tuple[float, float] | None, orientation: int, subsec: str | None) -> Image.Exif:
    exif = Image.Exif()
    if orientation != 1:
        exif[0x0112] = orientation
    if taken:
        ifd = {36867: taken.strftime("%Y:%m:%d %H:%M:%S")}
        if subsec:
            ifd[37521] = subsec
        exif[0x8769] = ifd
    if gps:
        def dms(v):
            v = abs(v)
            d, m = int(v), int((v - int(v)) * 60)
            return (IFDRational(d, 1), IFDRational(m, 1), IFDRational(round((v - d - m / 60) * 3600 * 100), 100))

        exif[0x8825] = {1: "N" if gps[0] >= 0 else "S", 2: dms(gps[0]), 3: "E" if gps[1] >= 0 else "W", 4: dms(gps[1])}
    return exif


def _stored(img: Image.Image, orientation: int) -> Image.Image:
    """The pixels to store so that the picture, once its orientation tag is obeyed, is `img`."""
    turns = {3: Image.Transpose.ROTATE_180, 6: Image.Transpose.ROTATE_90, 8: Image.Transpose.ROTATE_270}
    return img.transpose(turns[orientation]) if orientation in turns else img


def stamp(path: Path, mtime: datetime | None) -> Path:
    if mtime is not None:
        ts = mtime.timestamp()
        os.utime(path, (ts, ts))
    return path


def save(path: Path, img: Image.Image, taken: datetime | None = None, gps: tuple[float, float] | None = None,
         orientation: int = 1, quality: int = 88, subsec: str | None = None, mtime: datetime | None = None) -> Path:
    """Write a picture in the format its extension says (JPEG, PNG, WebP, TIFF), with the camera's notes if given."""
    path.parent.mkdir(parents=True, exist_ok=True)
    ext = path.suffix.lower()
    pixels = _stored(img, orientation)
    if ext in (".jpg", ".jpeg"):
        pixels.save(path, "JPEG", quality=quality, exif=_exif(taken, gps, orientation, subsec))
    elif ext == ".webp":
        pixels.save(path, "WEBP", quality=quality, exif=_exif(taken, gps, orientation, subsec))
    elif ext in (".tif", ".tiff"):
        pixels.save(path, "TIFF")
    else:
        pixels.save(path, "PNG")
    return stamp(path, mtime)


def save_heic(path: Path, img: Image.Image, mtime: datetime | None = None) -> Path | None:
    """A HEIC written by Windows' own encoder (None when this PC has no HEIF extension). It carries no camera notes:
    Windows' encoder can't write them, so a HEIC test photo is dated by its name or its folder."""
    try:
        import asyncio

        from winrt.windows.graphics.imaging import BitmapAlphaMode, BitmapEncoder, BitmapPixelFormat, SoftwareBitmap
        from winrt.windows.storage.streams import DataReader, InMemoryRandomAccessStream

        rgba = np.array(img.convert("RGBA"))[:, :, [2, 1, 0, 3]]

        async def go():
            bitmap = SoftwareBitmap.create_copy_from_buffer(rgba.tobytes(), BitmapPixelFormat.BGRA8, img.width,
                                                            img.height, BitmapAlphaMode.PREMULTIPLIED)
            stream = InMemoryRandomAccessStream()
            encoder = await BitmapEncoder.create_async(BitmapEncoder.heif_encoder_id, stream)
            encoder.set_software_bitmap(bitmap)
            await encoder.flush_async()
            stream.seek(0)
            reader = DataReader(stream)
            await reader.load_async(stream.size)
            return bytes(reader.read_buffer(stream.size))

        from mary.photos.read import on_fresh_thread

        data = on_fresh_thread(lambda: asyncio.run(go()))
    except Exception:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return stamp(path, mtime)


# ---------------------------------------------------------------- the awkward cases, with their answers


@dataclass
class Truth:
    rel: str                          # the file, under the library's root
    start: datetime | None = None     # the period this file says it was taken in: from…  (a copy's own notes, not the photo's)
    end: datetime | None = None       # …to (not included)
    precision: str = ""               # second | day | month | year | decade | "" (undated)
    source: str = "none"              # exif | filename | folder | file | none
    guess: bool = False               # her date for it must be marked a guess
    flag: str = ""                    # "disagree" when its own date and its folder's disagree
    same_as: str | None = None        # a copy of this other file: one photo between them
    state: str = "ok"                 # ok | unreadable | tiny
    lat: float | None = None
    lon: float | None = None
    text: str = ""                    # words printed in the picture


def D(y, m=1, d=1, h=0, mi=0, s=0) -> datetime:
    return datetime(y, m, d, h, mi, s)


def hard_set(root: Path, heic: bool = True) -> list[Truth]:
    """A library of the awkward cases. Returns the answer for every file."""
    root = Path(root)
    t: list[Truth] = []
    sz = (640, 480)

    def add(truth: Truth) -> Truth:
        t.append(truth)
        return truth

    # a camera roll: exact dates, some with a position, one turned on its side
    harbour = SITES["harbourton"]
    save(root / "Camera" / "IMG_0001.jpg", picture(1, sz), D(2023, 12, 25, 10, 15), harbour)
    add(Truth("Camera/IMG_0001.jpg", D(2023, 12, 25, 10, 15), D(2023, 12, 25, 10, 15, 1), "second", "exif",
              lat=harbour[0], lon=harbour[1]))
    save(root / "Camera" / "IMG_0002.jpg", picture(2, sz), D(2019, 7, 4, 18, 30, 12), orientation=6)
    add(Truth("Camera/IMG_0002.jpg", D(2019, 7, 4, 18, 30, 12), D(2019, 7, 4, 18, 30, 13), "second", "exif"))
    north = SITES["northfield"]
    save(root / "Camera" / "IMG_0003.webp", picture(3, sz), D(2021, 5, 1, 9, 0, 0), north)
    add(Truth("Camera/IMG_0003.webp", D(2021, 5, 1, 9), D(2021, 5, 1, 9, 0, 1), "second", "exif", lat=north[0], lon=north[1]))
    # a burst: two nearly identical frames a second apart. Two photos, never merged.
    burst = picture(4, sz)
    save(root / "Camera" / "IMG_0004.jpg", burst, D(2022, 3, 3, 12, 0, 0))
    save(root / "Camera" / "IMG_0005.jpg", near(burst, 1), D(2022, 3, 3, 12, 0, 1))
    add(Truth("Camera/IMG_0004.jpg", D(2022, 3, 3, 12), D(2022, 3, 3, 12, 0, 1), "second", "exif"))
    add(Truth("Camera/IMG_0005.jpg", D(2022, 3, 3, 12, 0, 1), D(2022, 3, 3, 12, 0, 2), "second", "exif"))
    # a sign with words in it
    save(root / "Camera" / "IMG_0006.jpg", with_sign(picture(6, (800, 520)), "HARBOUR CAFE open 8am - 6pm"),
         D(2022, 8, 20, 15, 5), harbour)
    add(Truth("Camera/IMG_0006.jpg", D(2022, 8, 20, 15, 5), D(2022, 8, 20, 15, 5, 1), "second", "exif",
              lat=harbour[0], lon=harbour[1], text="HARBOUR CAFE"))
    # a clock that was never set: zeros in the camera's date, and the file's own date to fall back on
    save(root / "Camera" / "IMG_0007.jpg", picture(7, sz), D(2000, 1, 1), mtime=D(2020, 2, 2, 8))
    add(Truth("Camera/IMG_0007.jpg", D(2020, 2, 2), D(2020, 2, 3), "day", "file", guess=True))

    # messengers strip the camera's notes but name the file with the date
    save(root / "Messenger" / "IMG-20211102-WA0007.jpg", picture(10, sz), mtime=D(2022, 1, 1))
    add(Truth("Messenger/IMG-20211102-WA0007.jpg", D(2021, 11, 2), D(2021, 11, 3), "day", "filename"))
    save(root / "Messenger" / "WhatsApp Image 2022-03-09 at 14.22.31.jpeg", picture(11, sz), mtime=D(2023, 1, 1))
    add(Truth("Messenger/WhatsApp Image 2022-03-09 at 14.22.31.jpeg", D(2022, 3, 9, 14, 22, 31),
              D(2022, 3, 9, 14, 22, 32), "second", "filename"))
    save(root / "Messenger" / "Screenshot_20240115-093012.png", picture(12, sz), mtime=D(2024, 6, 1))
    add(Truth("Messenger/Screenshot_20240115-093012.png", D(2024, 1, 15, 9, 30, 12), D(2024, 1, 15, 9, 30, 13), "second",
              "filename"))
    save(root / "Messenger" / "PXL_20230601_120500123.jpg", picture(13, sz), mtime=D(2023, 9, 9))
    add(Truth("Messenger/PXL_20230601_120500123.jpg", D(2023, 6, 1, 12, 5), D(2023, 6, 1, 12, 5, 1), "second", "filename"))
    # a number that looks like a date but isn't one (a phone number, a counter)
    save(root / "Messenger" / "IMG_99999999.jpg", picture(14, sz), mtime=D(2021, 7, 7))
    add(Truth("Messenger/IMG_99999999.jpg", D(2021, 7, 7), D(2021, 7, 8), "day", "file", guess=True))
    # a sticker: too small to be a photo
    save(root / "Messenger" / "sticker.webp", picture(15, (128, 128)))
    add(Truth("Messenger/sticker.webp", state="tiny"))

    # an album named for a time: the folder says when
    save(root / "Christmas 2019" / "a.jpg", picture(20, sz), mtime=D(2024, 1, 1))
    add(Truth("Christmas 2019/a.jpg", D(2019, 12, 1), D(2020, 1, 1), "month", "folder", guess=True))
    save(root / "Christmas 2019" / "b.jpg", picture(21, sz), mtime=D(2024, 1, 1))
    add(Truth("Christmas 2019/b.jpg", D(2019, 12, 1), D(2020, 1, 1), "month", "folder", guess=True))
    save(root / "Christmas 2019" / "c.jpg", picture(22, sz), D(2019, 12, 24, 20, 0))      # agrees with its folder
    add(Truth("Christmas 2019/c.jpg", D(2019, 12, 24, 20), D(2019, 12, 24, 20, 0, 1), "second", "exif"))
    save(root / "Christmas 2019" / "d.jpg", picture(23, sz), D(2024, 3, 1, 11, 0))        # a print photographed later
    add(Truth("Christmas 2019/d.jpg", D(2024, 3, 1, 11), D(2024, 3, 1, 11, 0, 1), "second", "exif", flag="disagree"))
    save(root / "Summer 2015" / "x.jpg", picture(24, sz), mtime=D(2024, 1, 1))
    add(Truth("Summer 2015/x.jpg", D(2015), D(2016), "year", "folder", guess=True))
    # scans of old prints: no camera, a sepia look, and a folder that names a decade
    for i in range(3):
        save(root / "Scans 1990s" / f"scan_{i:03}.jpg", as_scan(picture(30 + i, sz)), mtime=D(2021, 5, 5))
        add(Truth(f"Scans 1990s/scan_{i:03}.jpg", D(1990), D(2000), "decade", "folder", guess=True))
    # nothing says when but the file's own date
    save(root / "Misc" / "holiday.jpg", picture(40, sz), mtime=D(2020, 8, 15, 12))
    add(Truth("Misc/holiday.jpg", D(2020, 8, 15), D(2020, 8, 16), "day", "file", guess=True))

    # copies, in every form: nothing is counted twice
    save(root / "Backup" / "IMG_0001 copy.jpg", picture(1, sz), D(2023, 12, 25, 10, 15), harbour)  # the same pixels again
    add(Truth("Backup/IMG_0001 copy.jpg", D(2023, 12, 25, 10, 15), D(2023, 12, 25, 10, 15, 1), "second", "exif",
              same_as="Camera/IMG_0001.jpg", lat=harbour[0], lon=harbour[1]))
    small = picture(1, sz).resize((sz[0] // 2, sz[1] // 2), Image.Resampling.LANCZOS)      # smaller, re-saved, notes stripped
    save(root / "Shared" / "img1_small.jpg", small, quality=70, mtime=D(2024, 1, 5))
    add(Truth("Shared/img1_small.jpg", D(2024, 1, 5), D(2024, 1, 6), "day", "file", guess=True, same_as="Camera/IMG_0001.jpg"))
    save(root / "Shared" / "img2_turned.jpg", picture(2, sz), D(2019, 7, 4, 18, 30, 12), orientation=8)   # same view, another tag
    add(Truth("Shared/img2_turned.jpg", D(2019, 7, 4, 18, 30, 12), D(2019, 7, 4, 18, 30, 13), "second", "exif",
              same_as="Camera/IMG_0002.jpg"))

    # files that can't be read, and must not stop anything
    (root / "Camera").mkdir(parents=True, exist_ok=True)
    (root / "Camera" / "broken.jpg").write_bytes(os.urandom(3000))
    add(Truth("Camera/broken.jpg", state="unreadable"))
    (root / "Camera" / "empty.jpg").write_bytes(b"")
    add(Truth("Camera/empty.jpg", state="unreadable"))
    buf = io.BytesIO()
    picture(50, (800, 600)).save(buf, "JPEG", quality=90)
    (root / "Camera" / "cut_short.jpg").write_bytes(buf.getvalue()[: len(buf.getvalue()) // 3])
    add(Truth("Camera/cut_short.jpg", state="unreadable"))

    if heic:
        made = save_heic(root / "Trip June 2021" / "IMG_0100.heic", picture(60, sz), mtime=D(2022, 2, 2))
        if made is not None:
            add(Truth("Trip June 2021/IMG_0100.heic", D(2021, 6, 1), D(2021, 7, 1), "month", "folder", guess=True))
    return t


# ---------------------------------------------------------------- invented people (fingerprints with known answers)


class Cast:
    """Invented people for the face tests: no face is drawn and none is real; a fingerprint is a point on a sphere.

    A person is a centre; a photo of them is a point near it (`noise` is how far); growing older moves the centre along a path of
    its own. The numbers are set so that, in 128 dimensions, two photos of one person at one age have a cosine of about 0.7,
    two strangers about 0, a sibling's about 0.35, and the same person thirty years apart about 0.35: the awkward cases the
    rules have to be right about (they are invented, not SFace's measured numbers)."""

    DRIFT = 1.73 / 30          # per year: a centre thirty years on is 0.5 like itself

    def __init__(self, seed: int = 0, dim: int = 128):
        self.rng = np.random.default_rng(seed)
        self.dim = dim
        self.people: dict[str, tuple[np.ndarray, np.ndarray]] = {}

    def _random(self) -> np.ndarray:
        v = self.rng.normal(size=self.dim)
        return v / np.linalg.norm(v)

    def _apart_from(self, base: np.ndarray) -> np.ndarray:
        v = self._random()
        v = v - (v @ base) * base
        return v / np.linalg.norm(v)

    def person(self, name: str, like: str | None = None, closeness: float = 0.5) -> str:
        """A new person. With `like` they resemble that person (a sibling, a twin): their centres are `closeness` alike."""
        if like is None:
            centre = self._random()
        else:
            base = self.people[like][0]
            centre = closeness * base + (1 - closeness ** 2) ** 0.5 * self._apart_from(base)
        self.people[name] = (centre, self._apart_from(centre))
        return name

    def face(self, name: str, age: float = 0.0, noise: float = 0.65) -> np.ndarray:
        """One photo's fingerprint of this person `age` years after their first (or before, when negative)."""
        centre, drift = self.people[name]
        look = centre + self.DRIFT * age * drift
        v = look / np.linalg.norm(look) + noise * self.rng.normal(size=self.dim) / np.sqrt(self.dim)
        return (v / np.linalg.norm(v)).astype(np.float32)

    def strangers(self, n: int) -> list[np.ndarray]:
        """n people who each appear once: a crowd."""
        return [self._random().astype(np.float32) for _ in range(n)]


class StandIn:
    """A stand-in for the two face models, for tests: it recognises invented pictures from a table, by the picture's own
    difference hash, so it finds the same faces however the file was saved, shrunk or turned. It tests everything around the
    models (reading a picture the right way up, boxes, which faces are clear enough, grouping, naming, the worker) and nothing
    about how well a model sees a real face: that is measured with the real models (mary/photos/bench.py)."""

    tag = "stand-in-1"

    def __init__(self):
        self.table: list[tuple[int, list[tuple[tuple, np.ndarray, float]]]] = []
        self.detected = 0
        self.embedded = 0

    def teach(self, img: Image.Image, faces: list[tuple[tuple[float, float, float, float], np.ndarray, float]]) -> None:
        """This picture (as seen) has these faces: (box as fractions x, y, w, h; fingerprint; the detector's confidence)."""
        from mary.photos.read import dhash

        self.table.append((dhash(img), list(faces)))

    def detect(self, image: Image.Image):
        from mary.photos.faces import Detection
        from mary.photos.read import dhash, hamming

        self.detected += 1
        h = dhash(image)
        near = [(hamming(h, k), faces) for k, faces in self.table if hamming(h, k) <= 6]
        if not near:
            return []
        w, ht = image.size
        out = []
        for (x, y, bw, bh), vec, score in min(near, key=lambda t: t[0])[1]:
            box = (x * w, y * ht, bw * w, bh * ht)
            cx, cy = box[0] + box[2] / 2, box[1] + box[3] / 2
            points = ((cx - box[2] * 0.2, cy - box[3] * 0.15), (cx + box[2] * 0.2, cy - box[3] * 0.15), (cx, cy),
                      (cx - box[2] * 0.15, cy + box[3] * 0.25), (cx + box[2] * 0.15, cy + box[3] * 0.25))
            det = Detection(box, points, score)
            det.vec = vec
            out.append(det)
        return out

    def embed(self, image: Image.Image, detection) -> np.ndarray:
        self.embedded += 1
        return detection.vec


def grained(img: Image.Image, seed: int = 0) -> Image.Image:
    """The picture with fine grain over it, like a phone's sensor noise: a JPEG of it is a few MB at 12 megapixels, like a real
    photo's, instead of the few hundred KB a smooth invented picture compresses to (so a benchmark reads realistic files)."""
    rng = np.random.default_rng(seed)
    tile = rng.integers(-14, 15, size=(256, 256, 1), dtype=np.int16)
    reps = (img.height // 256 + 1, img.width // 256 + 1, 1)
    noise = np.tile(tile, reps)[: img.height, : img.width]
    return Image.fromarray(np.clip(np.asarray(img, dtype=np.int16) + noise, 0, 255).astype(np.uint8))


def bulk(root: Path, n: int = 500, seed: int = 1, size: tuple[int, int] = (2000, 1500), grain: bool = False) -> dict:
    """n realistic files for the benchmark: mostly dated by the camera, some by their name or folder, some scans, and about
    one in ten a copy of another. Returns the counts it should come to."""
    rng = random.Random(seed)
    root = Path(root)
    scenes = []
    made = {"files": 0, "photos": 0}
    start = D(2015, 1, 1)
    for i in range(n):
        kind = rng.random()
        copy = scenes and rng.random() < 0.10
        if copy:
            j = rng.randrange(len(scenes))
            img, info = scenes[j]
            how = rng.choice(["exact", "small", "again"])
            folder = root / "Copies"
            if how == "small":
                img = img.resize((img.width // 2, img.height // 2), Image.Resampling.LANCZOS)
            save(folder / f"copy_{i:04}.jpg", img, info.get("taken") if how != "small" else None, info.get("gps"))
            made["files"] += 1
            continue
        img = picture(seed * 100000 + i, size)
        if grain:
            img = grained(img, i)
        taken = start + timedelta(seconds=rng.randrange(0, 9 * 365 * 86400))
        gps = tuple(rng.choice(list(SITES.values()))) if rng.random() < 0.4 else None
        info = {"taken": taken, "gps": gps}
        if kind < 0.70:
            save(root / "Camera" / f"IMG_{i:04}.jpg", img, taken, gps)
        elif kind < 0.85:
            save(root / "Messenger" / f"IMG-{taken:%Y%m%d}-WA{i:04}.jpg", img, None, None)
            info = {"taken": None, "gps": None}
        elif kind < 0.93:
            img = as_scan(img)            # what is on disk is the scan: a copy of it is a copy of that
            save(root / f"Album {1990 + i % 30}" / f"p_{i:04}.jpg", img, mtime=taken)
            info = {"taken": None, "gps": None}
        else:
            save(root / "Misc" / f"photo_{i:04}.png", img, mtime=taken)
            info = {"taken": None, "gps": None}
        scenes.append((img, info))
        made["files"] += 1
        made["photos"] += 1
    return made
