"""The notification-area icon: left-click to talk, right-click for the menu.

Running in the background (at sign-in, with no console), this is where MARY shows that she's there
and where her own problems surface (no microphone, language model offline): as tray notices.
"""
from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

from PySide6.QtCore import QObject, QPointF, Qt, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPen, QPixmap, QRadialGradient
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from mary import autostart
from mary.config import user_config
from mary.output import Output
from mary.profile import profile_path

log = logging.getLogger(__name__)


def reactor_icon(size: int = 64) -> QIcon:
    """A small arc reactor, drawn in code like the HUD (no image files to ship)."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    c = size / 2
    glow = QRadialGradient(QPointF(c, c), c)
    glow.setColorAt(0.0, QColor(170, 245, 255, 255))
    glow.setColorAt(0.35, QColor(40, 200, 255, 220))
    glow.setColorAt(1.0, QColor(0, 120, 200, 0))
    p.setPen(Qt.NoPen)
    p.setBrush(glow)
    p.drawEllipse(QPointF(c, c), c * 0.98, c * 0.98)
    p.setBrush(Qt.NoBrush)
    for radius, width, alpha in ((0.80, 0.09, 255), (0.55, 0.07, 230)):
        p.setPen(QPen(QColor(120, 230, 255, alpha), size * width))
        p.drawEllipse(QPointF(c, c), c * radius, c * radius)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(235, 252, 255))
    p.drawEllipse(QPointF(c, c), c * 0.24, c * 0.24)
    p.end()
    return QIcon(pm)


def _open(path: Path) -> None:
    """Open a file for reading/editing: its usual app, else Notepad (.yaml often has no app)."""
    try:
        os.startfile(str(path))  # noqa: S606 - a file of MARY's own, chosen from the menu
    except OSError:
        subprocess.Popen(["notepad.exe", str(path)])


class Tray(QObject):
    """Create on the Qt thread. notice/tooltip are Signals, so any thread may emit them."""

    notice = Signal(str)
    tooltip = Signal(str)

    def __init__(self, app, cfg, on_talk, on_quit, log_file: Path):
        super().__init__()
        self.icon = QSystemTrayIcon(reactor_icon(), app)
        self.icon.setToolTip("MARY · starting…")
        menu = QMenu()
        talk = QAction(f"Talk to MARY   ({cfg.hotkey.combo})", menu)
        talk.triggered.connect(on_talk)
        menu.addAction(talk)
        menu.addSeparator()
        for label, path in (("What MARY remembers about you…", profile_path()),
                            ("Your settings (user/config.yaml)…", user_config()),
                            ("Open the log", log_file)):
            action = QAction(label, menu)
            action.triggered.connect(lambda _=False, p=path: _open(p))
            menu.addAction(action)
        self.startup = QAction("Start MARY when I sign in", menu)
        self.startup.setCheckable(True)
        self.startup.setChecked(autostart.is_enabled())
        self.startup.toggled.connect(self._toggle_startup)
        menu.addAction(self.startup)
        menu.addSeparator()
        quit_ = QAction("Quit MARY", menu)
        quit_.triggered.connect(on_quit)
        menu.addAction(quit_)
        self.menu = menu  # keep a reference: Qt doesn't own a context menu set from Python
        self.icon.setContextMenu(menu)
        self.icon.activated.connect(
            lambda reason: on_talk() if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
        self.notice.connect(lambda text: self.icon.showMessage("MARY", text, reactor_icon(), 6000))
        self.tooltip.connect(self.icon.setToolTip)
        self.icon.show()
        log.info("tray icon %s", "shown" if self.icon.isVisible() else "not shown (no notification area?)")

    def _toggle_startup(self, on: bool) -> None:
        try:
            autostart.enable() if on else autostart.disable()
            self.notice.emit("MARY will start when you sign in." if on else "MARY won't start at sign-in.")
        except Exception as e:
            log.exception("autostart change failed")
            self.notice.emit(f"Couldn't change that: {e}")
            self.startup.blockSignals(True)
            self.startup.setChecked(autostart.is_enabled())
            self.startup.blockSignals(False)


class TrayOutput(Output):
    """Shows MARY's own status notices (microphone, language model, speaker) as tray notifications."""

    def __init__(self, tray: Tray):
        self.tray = tray

    def status(self, text: str) -> None:
        self.tray.notice.emit(text.lstrip("⚠✓▸ "))
