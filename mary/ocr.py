"""Read text from any window with Windows' built-in OCR (Windows.Media.Ocr, fully local).

Used where UI Automation can't see the content, e.g. web pages in Edge. A full window
takes ~40 ms to capture and ~0.3 s to recognise on the Ryzen 5 PRO 4650U.
"""
from __future__ import annotations

import asyncio
import ctypes
from dataclasses import dataclass, field

import numpy as np


@dataclass
class OcrLine:
    text: str
    x: float          # bounding box in window pixels
    y: float
    w: float
    h: float
    words: list[tuple[str, float, float]] = field(default_factory=list)  # (text, left, right)

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def cy(self) -> float:
        return self.y + self.h / 2


def capture_window(hwnd: int) -> np.ndarray:
    """BGRA pixels of a window, even when it is partly covered (PrintWindow, full-content mode)."""
    import win32gui
    import win32ui

    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    w, h = right - left, bottom - top
    hdc = win32gui.GetWindowDC(hwnd)
    src = win32ui.CreateDCFromHandle(hdc)
    mem = src.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    try:
        bmp.CreateCompatibleBitmap(src, w, h)
        mem.SelectObject(bmp)
        ctypes.windll.user32.PrintWindow(hwnd, mem.GetSafeHdc(), 2)  # PW_RENDERFULLCONTENT
        pixels = np.frombuffer(bmp.GetBitmapBits(True), dtype=np.uint8).reshape(h, w, 4)
    finally:
        mem.DeleteDC()
        src.DeleteDC()
        win32gui.ReleaseDC(hwnd, hdc)
        win32gui.DeleteObject(bmp.GetHandle())
    return pixels


def ocr_pixels(pixels: np.ndarray, offset: tuple[int, int] = (0, 0)) -> list[OcrLine]:
    """OCR a BGRA image. `offset` is added to boxes, so crops report window coordinates."""
    from winrt.windows.graphics.imaging import BitmapAlphaMode, BitmapPixelFormat, SoftwareBitmap
    from winrt.windows.media.ocr import OcrEngine

    h, w = pixels.shape[:2]
    bitmap = SoftwareBitmap.create_copy_from_buffer(np.ascontiguousarray(pixels).tobytes(),
                                                    BitmapPixelFormat.BGRA8, w, h, BitmapAlphaMode.PREMULTIPLIED)
    engine = OcrEngine.try_create_from_user_profile_languages()
    if engine is None:
        raise RuntimeError("Windows has no OCR language installed")

    async def recognise():
        return await engine.recognize_async(bitmap)

    result = asyncio.run(recognise())
    lines = []
    for line in result.lines:
        boxes = [word.bounding_rect for word in line.words]
        x0 = min(b.x for b in boxes)
        y0 = min(b.y for b in boxes)
        x1 = max(b.x + b.width for b in boxes)
        y1 = max(b.y + b.height for b in boxes)
        words = [(word.text, b.x + offset[0], b.x + b.width + offset[0]) for word, b in zip(line.words, boxes)]
        lines.append(OcrLine(line.text, x0 + offset[0], y0 + offset[1], x1 - x0, y1 - y0, words))
    return lines


def ocr_window(hwnd: int, crop: tuple[float, float, float, float] | None = None) -> list[OcrLine]:
    """OCR a window, optionally only a region given as fractions (x0, y0, x1, y1) of its size."""
    pixels = capture_window(hwnd)
    if not crop:
        return ocr_pixels(pixels)
    h, w = pixels.shape[:2]
    x0, y0, x1, y1 = int(crop[0] * w), int(crop[1] * h), int(crop[2] * w), int(crop[3] * h)
    return ocr_pixels(pixels[y0:y1, x0:x1], offset=(x0, y0))


def lines_to_text(lines: list[OcrLine]) -> str:
    """Reading order: top to bottom, then left to right within a row."""
    ordered = sorted(lines, key=lambda l: (round(l.cy / 12), l.x))
    return "\n".join(l.text for l in ordered)
