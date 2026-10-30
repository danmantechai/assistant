"""The HUD window: frameless, always on top, per-pixel transparent, click-through, never focused.

PySide6 + QWebEngineView. (pywebview was tried first: on Windows its WebView2 window draws an opaque
background, so the "floating rings" look is impossible there. Qt's translucent windows composite
per pixel, glow included.)

Thread model: Qt lives on the main thread. Everything else (the REPL, the agent, tools) runs on worker
threads and talks to this window only through its Signals, which Qt delivers on the main thread.
"""
from __future__ import annotations

import ctypes
import logging
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWebEngineWidgets import QWebEngineView

log = logging.getLogger(__name__)

WEB_DIR = Path(__file__).parent / "web"
_GWL_EXSTYLE = -20
_WS_EX_CLICKTHROUGH = 0x00080000 | 0x00000020 | 0x08000000  # LAYERED | TRANSPARENT | NOACTIVATE


class HudWindow(QWebEngineView):
    js = Signal(str)          # a JavaScript call to run in the page (from any thread)
    level = Signal(float)     # audio level 0..1, sent to the page at most ~30 times a second
    command = Signal(str)     # "activity" | "idle" | "toggle" | "hide_now" | "show_now"

    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg.hud
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                            | Qt.WindowDoesNotAcceptFocus | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setStyleSheet("background: transparent")
        self.page().setBackgroundColor(QColor(0, 0, 0, 0))
        self.resize(self.cfg.width, self.cfg.height)

        self._ready = False
        self._queue: list[str] = []
        self._level: float | None = None
        self._wanted = False            # should the HUD be up right now?
        self.loadFinished.connect(self._on_load)
        self.load(QUrl.fromLocalFile(str(WEB_DIR / "index.html")))

        # Connected to methods of this QObject, so Qt queues them onto the main thread.
        self.js.connect(self._enqueue)
        self.level.connect(self._set_level)
        self.command.connect(self._on_command)
        self._flush_timer = QTimer(self, interval=33, timeout=self._flush)   # batch JS calls (~30 Hz)
        self._flush_timer.start()
        self._hide_timer = QTimer(self, singleShot=True, timeout=self._hide_if_done)
        # Voice mode sets this to "is MARY still busy with you?" (listening, thinking or speaking): the
        # "idle" signal comes when she's done *thinking*, but a long answer is still being spoken then,
        # and the HUD used to fade out in the middle of it.
        self.busy = lambda: False
        self._gone_timer = QTimer(self, singleShot=True, timeout=self._really_hide)

    # ------------------------------------------------------------------ page bridge

    def _on_load(self, ok: bool) -> None:
        self._ready = ok
        if not ok:
            log.error("HUD page failed to load from %s", WEB_DIR)

    def _enqueue(self, code: str) -> None:
        self._queue.append(code)

    def _set_level(self, value: float) -> None:
        self._level = value

    def _flush(self) -> None:
        if not self._ready:
            return
        calls, self._queue[:] = list(self._queue), []
        if self._level is not None and self.isVisible():
            calls.append(f"hud.level({self._level:.3f})")
            self._level = None
        if calls:
            self.page().runJavaScript(";".join(calls))

    # ------------------------------------------------------------------ showing and hiding

    def _place(self) -> None:
        area = QGuiApplication.primaryScreen().availableGeometry()  # excludes the taskbar
        m = self.cfg.margin
        if self.cfg.position == "center":
            x, y = area.center().x() - self.width() // 2, area.center().y() - self.height() // 2
        else:  # bottom-right
            x, y = area.right() - self.width() - m, area.bottom() - self.height() - m
        self.move(x, y)

    def _make_click_through(self) -> None:
        """Belt and braces on top of WindowTransparentForInput: the HUD must never catch a click,
        including the ones MARY's own skills send (WhatsApp's filter, Spotify's play button)."""
        hwnd = int(self.winId())
        user32 = ctypes.windll.user32
        user32.SetWindowLongW(hwnd, _GWL_EXSTYLE, user32.GetWindowLongW(hwnd, _GWL_EXSTYLE) | _WS_EX_CLICKTHROUGH)

    def summon(self) -> None:
        self._wanted = True
        self._hide_timer.stop()
        self._gone_timer.stop()
        if not self.isVisible():
            self._place()
            self.show()
            self._make_click_through()
            self._queue.append("hud.show()")

    def _hide_if_done(self) -> None:
        if self.busy():
            self._hide_timer.start(1000)  # look again in a second
        else:
            self.dismiss()

    def dismiss(self) -> None:
        self._wanted = False
        self._hide_timer.stop()
        self._queue.append("hud.hide()")
        self._gone_timer.start(450)  # after the fade-out, hide the window so nothing renders

    def _really_hide(self) -> None:
        if not self._wanted:
            self.hide()

    def _on_command(self, cmd: str) -> None:
        if cmd == "activity":
            self.summon()
        elif cmd == "idle":
            self._hide_timer.start(int(self.cfg.hide_after_s * 1000))
        elif cmd == "toggle":
            if self._wanted:
                self.dismiss()
            else:
                self.summon()
                self._queue.append("hud.state('listening')")
                self._hide_timer.start(int(self.cfg.hide_after_s * 1000) * 2)
        elif cmd == "hide_now":   # e.g. for take_screenshot, so the HUD isn't in the picture
            self._queue.clear()
            self.hide()
        elif cmd == "show_now" and self._wanted:
            self.show()
            self._make_click_through()
