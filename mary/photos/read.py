"""Reading one picture: decode it (Pillow, or Windows' own decoder for HEIC), turn it the right way up, and take from it
what code can know exactly: the time and GPS position the camera wrote, a thumbnail, and two fingerprints (the file's
bytes, and a 64-bit difference hash of the picture as it looks, which a copy, a resized or a re-saved version keeps).

Measured on this laptop: a JPEG's reduced decode takes ~5 ms (a full one ~11 ms), a HEIC ~28 ms through Windows.
Nothing here is kept but the thumbnail's bytes and the numbers; the caller decides what to store.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import re
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from struct import error as struct_error

import numpy as np
from PIL import Image, UnidentifiedImageError

PILLOW_TYPES = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp", ".gif"}
WINDOWS_TYPES = {".heic", ".heif"}      # Pillow can't; Windows can, with its HEIF extension
EXTENSIONS = PILLOW_TYPES | WINDOWS_TYPES
MAX_PIXELS = 100_000_000                # past this a file is left alone (a panorama, or a trap)
MAX_BYTES = 250 * 1024 * 1024
THUMB_PX = 320
FACT_PX = 640                           # what the thumbnail and the fingerprints are made from

Image.MAX_IMAGE_PIXELS = None           # we check MAX_PIXELS ourselves, before decoding


class Unreadable(Exception):
    """This file can't be read as a picture; the message is a short reason (it goes in the record, never a path)."""


@dataclass
class Decoded:
    image: Image.Image          # RGB, the right way up, at most `max_side` on its long side
    width: int                  # the whole picture, as seen
    height: int
    taken: datetime | None      # what the camera wrote (local time as written); None when it wrote nothing usable
    taken_exact: bool           # with a fraction of a second: bursts can be told apart
    lat: float | None
    lon: float | None


@dataclass
class Facts:
    sha: str
    dhash: str                  # 16 hex digits
    width: int
    height: int
    thumb: bytes                # JPEG
    taken: datetime | None
    taken_exact: bool
    lat: float | None
    lon: float | None
    size: int
    sharpness: float = 0.0


# ---------------------------------------------------------------- what the camera wrote

_EXIF_TIME = re.compile(r"^\s*(\d{4}):(\d{2}):(\d{2})[ T](\d{2}):(\d{2}):(\d{2})")


def exif_time(text, fraction=None) -> datetime | None:
    """'2023:12:25 10:15:00' -> a time, or None (a camera that has never had its clock set writes zeros)."""
    m = _EXIF_TIME.match(str(text or ""))
    if not m:
        return None
    try:
        t = datetime(*(int(g) for g in m.groups()))
    except ValueError:
        return None
    if fraction is not None and str(fraction).strip().isdigit():
        t = t.replace(microsecond=int((str(fraction).strip() + "000000")[:6]))
    return t


def _dms(value, ref) -> float | None:
    try:
        deg, minutes, seconds = (float(v) for v in value)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    out = deg + minutes / 60 + seconds / 3600
    return -out if str(ref).upper() in ("S", "W") else out


def position(lat, lon) -> tuple[float | None, float | None]:
    """A GPS position, or (None, None) when it can't be one (outside the globe, or a camera's 0, 0 for "no fix")."""
    if lat is None or lon is None or abs(lat) > 90 or abs(lon) > 180 or (abs(lat) < 1e-6 and abs(lon) < 1e-6):
        return None, None
    return round(lat, 6), round(lon, 6)


def _pillow_meta(im: Image.Image):
    exif = im.getexif()
    ifd = exif.get_ifd(0x8769)
    raw = ifd.get(36867) or ifd.get(36868)
    taken = exif_time(raw, ifd.get(37521) if raw == ifd.get(36867) else ifd.get(37522))
    gps = exif.get_ifd(0x8825)
    lat, lon = position(_dms(gps.get(2), gps.get(1)) if gps.get(2) else None,
                        _dms(gps.get(4), gps.get(3)) if gps.get(4) else None) if gps else (None, None)
    return exif.get(0x0112, 1), taken, lat, lon


# ---------------------------------------------------------------- decoding


def _flatten(im: Image.Image) -> Image.Image:
    """RGB from any mode (a transparent PNG goes onto white, not black)."""
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        rgba = im.convert("RGBA")
        base = Image.new("RGB", rgba.size, (255, 255, 255))
        base.paste(rgba, mask=rgba.split()[3])
        return base
    if im.mode in ("I;16", "I;16L", "I;16B", "I"):
        arr = np.asarray(im, dtype=np.float32)
        top = arr.max() or 1.0
        return Image.fromarray((arr / top * 255).astype(np.uint8)).convert("RGB")
    return im.convert("RGB")


def _decode_pillow(data: bytes, max_side: int) -> Decoded:
    try:
        im = Image.open(io.BytesIO(data))
        width, height = im.size
        if width * height > MAX_PIXELS:
            raise Unreadable("too large to read")
        orientation, taken, lat, lon = _pillow_meta(im)
        if im.format == "JPEG":
            im.draft("RGB", (max_side, max_side))     # the decoder skips most of the work for a small picture
        im.load()
    except Unreadable:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, EOFError, Image.DecompressionBombError,
            MemoryError, struct_error) as e:
        raise Unreadable("not a picture MARY can read" if isinstance(e, (UnidentifiedImageError, SyntaxError))
                         else "damaged or cut short") from None
    if orientation in (5, 6, 7, 8):
        width, height = height, width
    rgb = _turn(_flatten(im), orientation)
    rgb.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    return Decoded(rgb, width, height, taken, bool(taken and taken.microsecond), lat, lon)


def _turn(im: Image.Image, orientation: int) -> Image.Image:
    ops = {2: Image.Transpose.FLIP_LEFT_RIGHT, 3: Image.Transpose.ROTATE_180, 4: Image.Transpose.FLIP_TOP_BOTTOM,
           5: Image.Transpose.TRANSPOSE, 6: Image.Transpose.ROTATE_270, 7: Image.Transpose.TRANSVERSE,
           8: Image.Transpose.ROTATE_90}
    return im.transpose(ops[orientation]) if orientation in ops else im



def on_fresh_thread(fn):
    """Run `fn` (Windows' own async APIs: the HEIC decoder and encoder, OCR) on a thread of its own and wait for it. A thread that
    came into COM as a single-threaded apartment (comtypes and pywinauto make the main thread one, and the tests import them)
    never gets an answer from an awaited Windows call and waits for ever; a fresh thread is a free-threaded one and does."""
    box: dict = {}

    def work():
        try:
            box["value"] = fn()
        except BaseException as e:   # handed back to the caller's thread
            box["error"] = e

    t = threading.Thread(target=work, name="windows-call", daemon=True)
    t.start()
    t.join(timeout=300)
    if t.is_alive():
        raise Unreadable("took too long to read")
    if "error" in box:
        raise box["error"]
    return box["value"]


def _unwrap(value):
    from winrt.windows.foundation import IPropertyValue

    return value.as_(IPropertyValue)


def _decode_windows(data: bytes, max_side: int) -> Decoded:
    """HEIC (and HEIF) through Windows' decoder, which needs the 'HEIF Image Extensions' from the Store."""
    from winrt.windows.graphics.imaging import (BitmapAlphaMode, BitmapDecoder, BitmapPixelFormat, BitmapTransform,
                                                ColorManagementMode, ExifOrientationMode)
    from winrt.windows.storage.streams import Buffer, DataWriter, InMemoryRandomAccessStream

    async def go():
        stream = InMemoryRandomAccessStream()
        writer = DataWriter(stream)
        writer.write_bytes(data)
        await writer.store_async()
        await writer.flush_async()
        stream.seek(0)
        decoder = await BitmapDecoder.create_async(stream)
        width, height = decoder.oriented_pixel_width, decoder.oriented_pixel_height
        if width * height > MAX_PIXELS:
            raise Unreadable("too large to read")
        # the whole picture, turned the right way up by Windows; it is shrunk below (a scaling transform and the
        # rotation disagree about which way round the sizes go, so it is not asked to do both)
        bitmap = await decoder.get_software_bitmap_async(
            BitmapPixelFormat.RGBA8, BitmapAlphaMode.STRAIGHT, BitmapTransform(),
            ExifOrientationMode.RESPECT_EXIF_ORIENTATION, ColorManagementMode.DO_NOT_COLOR_MANAGE)
        buffer = Buffer(bitmap.pixel_width * bitmap.pixel_height * 4)
        bitmap.copy_to_buffer(buffer)
        image = Image.frombytes("RGBA", (bitmap.pixel_width, bitmap.pixel_height), bytes(buffer))
        names = ["System.Photo.DateTaken", "System.GPS.LatitudeRef", "System.GPS.LatitudeNumerator",
                 "System.GPS.LatitudeDenominator", "System.GPS.LongitudeRef", "System.GPS.LongitudeNumerator",
                 "System.GPS.LongitudeDenominator"]
        props = {}
        for name in names:   # asked one at a time: one the file lacks must not hide the others
            try:
                got = await decoder.bitmap_properties.get_properties_async([name])
                if name in list(got.keys()):
                    props[name] = _unwrap(got.lookup(name).value)
            except Exception:
                continue
        return image, width, height, props

    try:
        image, width, height, props = on_fresh_thread(lambda: asyncio.run(go()))
    except Unreadable:
        raise
    except OSError:
        raise Unreadable("needs the HEIF Image Extensions from the Microsoft Store") from None
    except Exception:
        raise Unreadable("not a picture MARY can read") from None

    def number(key):
        try:
            return [float(v) for v in props[key].get_double_array()]
        except Exception:
            return None

    taken = None
    if "System.Photo.DateTaken" in props:   # Windows hands the camera's local time back tagged as UTC: keep it as written
        try:
            t = props["System.Photo.DateTaken"].get_date_time()
            taken = t.replace(tzinfo=None, microsecond=0)
        except Exception:
            taken = None
    lat = lon = None
    for key, ref in (("Latitude", "System.GPS.LatitudeRef"), ("Longitude", "System.GPS.LongitudeRef")):
        num, den = number(f"System.GPS.{key}Numerator"), number(f"System.GPS.{key}Denominator")
        try:
            value = _dms([n / d for n, d in zip(num, den)], props[ref].get_string())
        except Exception:
            value = None
        lat, lon = (value, lon) if key == "Latitude" else (lat, value)
    lat, lon = position(lat, lon)
    image = _flatten(image)
    image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    return Decoded(image, width, height, taken, False, lat, lon)


def decode(data: bytes, ext: str, max_side: int = FACT_PX) -> Decoded:
    ext = ext.lower()
    if ext in WINDOWS_TYPES:
        return _decode_windows(data, max_side)
    return _decode_pillow(data, max_side)


# ---------------------------------------------------------------- fingerprints and thumbnail


def dhash(image: Image.Image) -> int:
    """64 bits: whether each of 8 x 8 pixels (the picture shrunk to 9 x 8 grey) is brighter than the one to its right.
    A copy, a smaller version or a re-saved one keeps it; a different photo doesn't."""
    small = np.asarray(image.convert("L").resize((9, 8), Image.Resampling.BOX), dtype=np.int16)
    bits = (small[:, 1:] > small[:, :-1]).flatten()
    out = 0
    for bit in bits:
        out = (out << 1) | int(bit)
    return out


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def sharpness(image: Image.Image) -> float:
    """0..1: how sharp the picture is (variance of the discrete Laplacian on a small grey copy)."""
    g = np.asarray(image.convert("L"), dtype=np.float32)
    if g.shape[0] < 4 or g.shape[1] < 4:
        return 0.0
    lap = g[:-2, 1:-1] + g[2:, 1:-1] + g[1:-1, :-2] + g[1:-1, 2:] - 4.0 * g[1:-1, 1:-1]
    return round(min(1.0, float(lap.var()) / 1500.0), 4)


def thumbnail(image: Image.Image, px: int = THUMB_PX) -> bytes:
    t = image.copy()
    t.thumbnail((px, px), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    t.save(out, "JPEG", quality=82)
    return out.getvalue()


def read_file(path: Path) -> Facts:
    """Everything code can know about one picture file at once. Raises Unreadable with a reason."""
    path = Path(path)
    ext = path.suffix.lower()
    try:
        size = path.stat().st_size
        if size == 0:
            raise Unreadable("empty")
        if size > MAX_BYTES:
            raise Unreadable("too large to read")
        data = path.read_bytes()
    except OSError:
        raise Unreadable("couldn't be opened") from None
    d = decode(data, ext)
    return Facts(hashlib.sha256(data).hexdigest(), f"{dhash(d.image):016x}", d.width, d.height, thumbnail(d.image),
                 d.taken, d.taken_exact, d.lat, d.lon, size, sharpness(d.image))


def windows_can_read_heic() -> bool:
    """Is Windows' HEIF decoder there? (Without it HEIC photos are listed as needing the extension.)"""
    try:
        import winrt.windows.graphics.imaging as imaging

        return on_fresh_thread(lambda: any(str(i.friendly_name) == "Microsoft HEIF Decoder"
                                           for i in imaging.BitmapDecoder.get_decoder_information_enumerator()))
    except Exception:
        return False
