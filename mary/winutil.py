"""Thin ctypes/pywin32 helpers for the Windows APIs MARY needs.

Kept dependency-light on purpose: tools import from here instead of pulling in
pywinauto/pyautogui for simple things like focusing a window or typing text.
"""
from __future__ import annotations

import ctypes
import os
import threading
import time
import uuid
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

import psutil

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32")
ole32 = ctypes.WinDLL("ole32")
dwmapi = ctypes.WinDLL("dwmapi")

# Declare pointer-sized handle types so 64-bit HWNDs are never truncated.
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetWindow.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wintypes.UINT]
kernel32.GetConsoleWindow.restype = wintypes.HWND
dwmapi.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]


# --------------------------------------------------------------------------- setup

def enable_dpi_awareness() -> None:
    """Per-monitor DPI awareness, so window rects and screenshots use real pixels."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass


def enable_console_colors() -> None:
    """Turn on ANSI escape handling in the classic Windows console."""
    handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
    mode = wintypes.DWORD()
    if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
        kernel32.SetConsoleMode(handle, mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING


_instance_mutex = None


def single_instance(name: str, wait_s: float = 0) -> bool:
    """False if another MARY already holds the named mutex. Two voice instances fight over the
    hotkey and the mic (and both answer), which is easy to cause with start-at-sign-in plus run.bat.
    wait_s: keep trying this long (after a restart, the old MARY may still be closing)."""
    import time

    global _instance_mutex
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateMutexW.restype = wintypes.HANDLE
    deadline = time.monotonic() + wait_s
    while True:
        handle = k32.CreateMutexW(None, False, f"Local\\{name}")
        if ctypes.get_last_error() != 183:  # ERROR_ALREADY_EXISTS
            _instance_mutex = handle  # held until the process exits
            return True
        k32.CloseHandle(handle)
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.5)


_com_state = threading.local()


def ensure_com() -> None:
    """CoInitialize once per thread (pycaw, WScript.Shell and ADODB all need it)."""
    if not getattr(_com_state, "ready", False):
        import pythoncom

        pythoncom.CoInitialize()
        _com_state.ready = True


# --------------------------------------------------------------------------- known folders

class _GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    def __init__(self, text: str):
        u = uuid.UUID(text)
        super().__init__(u.time_low, u.time_mid, u.time_hi_version,
                         (ctypes.c_ubyte * 8).from_buffer_copy(u.bytes[8:]))


_FOLDER_IDS = {
    "Desktop": "B4BFCC3A-DB2C-424C-B029-7FE99A87C641",
    "Documents": "FDD39AD0-238F-46AF-ADB4-6C85480369C7",
    "Downloads": "374DE290-123F-4565-9164-39C4925E467B",
    "Pictures": "33E28130-4E1E-4676-835A-98395C3BC3BB",
    "Music": "4BD8D571-6D19-48D3-BE97-422220080E43",
    "Videos": "18989B1D-99B5-455B-841C-AB7C74E4DDFC",
}


def known_folder(name: str) -> Path:
    """Real location of a user folder (follows OneDrive redirection)."""
    guid = _GUID(_FOLDER_IDS[name])
    buf = ctypes.c_wchar_p()
    if shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(buf)) != 0:
        return Path.home() / name
    try:
        return Path(buf.value)
    finally:
        ole32.CoTaskMemFree(buf)


# --------------------------------------------------------------------------- windows

@dataclass
class WindowInfo:
    hwnd: int
    title: str
    pid: int
    exe: str  # lower-case process image name, e.g. "spotify.exe"


def _window_pid(hwnd: int) -> int:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def _process_name(pid: int) -> str:
    try:
        return psutil.Process(pid).name().lower()
    except (psutil.Error, ValueError):
        return ""


def window_title(hwnd: int) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def _is_cloaked(hwnd: int) -> bool:
    cloaked = wintypes.DWORD()
    dwmapi.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked))  # DWMWA_CLOAKED
    return cloaked.value != 0


def list_windows() -> list[WindowInfo]:
    """Visible, titled, top-level app windows (what Alt+Tab would show, roughly)."""
    GWL_EXSTYLE, WS_EX_TOOLWINDOW, GW_OWNER = -20, 0x00000080, 4
    found: list[WindowInfo] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _):
        if not user32.IsWindowVisible(hwnd) or user32.GetWindow(hwnd, GW_OWNER):
            return True
        if user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW or _is_cloaked(hwnd):
            return True
        title = window_title(hwnd)
        if title and title not in ("Program Manager", "Windows Input Experience"):
            pid = _window_pid(hwnd)
            found.append(WindowInfo(hwnd, title, pid, _process_name(pid)))
        return True

    user32.EnumWindows(callback, 0)
    return found


def foreground_window() -> WindowInfo | None:
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    pid = _window_pid(hwnd)
    return WindowInfo(hwnd, window_title(hwnd), pid, _process_name(pid))


def is_own_console(hwnd: int) -> bool:
    """True if hwnd is the terminal MARY itself is running in (text mode)."""
    if hwnd == kernel32.GetConsoleWindow():
        return True
    pid = _window_pid(hwnd)
    try:
        me = psutil.Process(os.getpid())
        return pid == me.pid or pid in {p.pid for p in me.parents()}
    except psutil.Error:
        return False


def console_window() -> int | None:
    """The visible window MARY's terminal lives in (classic console or Windows Terminal)."""
    hwnd = kernel32.GetConsoleWindow()
    if hwnd and user32.IsWindowVisible(hwnd):
        return hwnd
    try:  # Windows Terminal: the console window is a hidden stand-in; find the terminal's own window
        me = psutil.Process(os.getpid())
        family = {me.pid} | {p.pid for p in me.parents()}
    except psutil.Error:
        return None
    return next((w.hwnd for w in list_windows() if w.pid in family), None)


def focus_window(hwnd: int) -> bool:
    """Bring a window to the front despite Windows' foreground-lock rules."""
    SW_RESTORE, SW_SHOW = 9, 5
    fg = user32.GetForegroundWindow()
    fg_thread = user32.GetWindowThreadProcessId(fg, None)
    me_thread = kernel32.GetCurrentThreadId()
    attached = fg_thread and fg_thread != me_thread and user32.AttachThreadInput(me_thread, fg_thread, True)
    try:
        user32.ShowWindow(hwnd, SW_RESTORE if user32.IsIconic(hwnd) else SW_SHOW)
        user32.SetForegroundWindow(hwnd)
        user32.BringWindowToTop(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(me_thread, fg_thread, False)
    time.sleep(0.05)
    if user32.GetForegroundWindow() == hwnd:
        return True
    try:  # pywinauto has a few more tricks; import lazily because it is slow to load
        from pywinauto.controls.hwndwrapper import HwndWrapper

        HwndWrapper(hwnd).set_focus()
    except Exception:
        pass
    return user32.GetForegroundWindow() == hwnd


def wait_input_idle(pid: int, timeout_ms: int = 3000) -> None:
    """Wait until a (just started) app is ready for keyboard input, as far as Windows can tell."""
    handle = kernel32.OpenProcess(0x00100000 | 0x0400, False, pid)  # SYNCHRONIZE | QUERY_INFORMATION
    if handle:
        try:
            user32.WaitForInputIdle(handle, timeout_ms)
        finally:
            kernel32.CloseHandle(handle)


def show_window(hwnd: int, action: str) -> None:
    codes = {"minimize": 6, "maximize": 3, "restore": 9}
    user32.ShowWindow(hwnd, codes[action])


def snap_window(hwnd: int, side: str) -> None:
    """Fill the left or right half of the window's monitor work area."""
    import win32api

    if user32.IsZoomed(hwnd) or user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    monitor = win32api.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
    left, top, right, bottom = win32api.GetMonitorInfo(monitor)["Work"]
    half = (right - left) // 2
    x, w = (left, half) if side == "left" else (left + half, right - left - half)
    y, h = top, bottom - top

    # Windows 10/11 windows have invisible resize borders; compensate so halves meet exactly.
    rect, frame = wintypes.RECT(), wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    if dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(frame), ctypes.sizeof(frame)) == 0:
        dl, dt = frame.left - rect.left, frame.top - rect.top
        dr, db = rect.right - frame.right, rect.bottom - frame.bottom
        x, y, w, h = x - dl, y - dt, w + dl + dr, h + dt + db
    user32.SetWindowPos(hwnd, 0, x, y, w, h, 0x0004 | 0x0010)  # SWP_NOZORDER | SWP_NOACTIVATE


def close_window(hwnd: int) -> None:
    """Polite close (like clicking X), so apps can still prompt to save."""
    user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE


# --------------------------------------------------------------------------- keyboard input

_ULONG_PTR = ctypes.c_size_t


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", _ULONG_PTR)]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", _ULONG_PTR)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT)]


class _INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


_KEYEVENTF_EXTENDEDKEY, _KEYEVENTF_KEYUP, _KEYEVENTF_UNICODE = 0x1, 0x2, 0x4


def _send(inputs: list[_INPUT]) -> None:
    arr = (_INPUT * len(inputs))(*inputs)
    user32.SendInput(len(inputs), arr, ctypes.sizeof(_INPUT))


def _key(vk: int = 0, scan: int = 0, flags: int = 0) -> _INPUT:
    return _INPUT(type=1, ki=_KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags))


def tap_vk(vk: int, extended: bool = False) -> None:
    flags = _KEYEVENTF_EXTENDEDKEY if extended else 0
    _send([_key(vk, flags=flags), _key(vk, flags=flags | _KEYEVENTF_KEYUP)])


def type_unicode(text: str, chunk: int = 40, pause: float = 0.01) -> None:
    """Type any text (accents, emoji, non-Latin) into the focused window."""
    VK_RETURN, VK_TAB = 0x0D, 0x09
    events: list[_INPUT] = []
    for ch in text.replace("\r\n", "\n"):
        if ch == "\n":
            events += [_key(VK_RETURN), _key(VK_RETURN, flags=_KEYEVENTF_KEYUP)]
            continue
        if ch == "\t":
            events += [_key(VK_TAB), _key(VK_TAB, flags=_KEYEVENTF_KEYUP)]
            continue
        data = ch.encode("utf-16-le")
        for i in range(0, len(data), 2):  # surrogate pairs become two code units
            unit = int.from_bytes(data[i:i + 2], "little")
            events += [_key(scan=unit, flags=_KEYEVENTF_UNICODE),
                       _key(scan=unit, flags=_KEYEVENTF_UNICODE | _KEYEVENTF_KEYUP)]
    for i in range(0, len(events), chunk * 2):
        _send(events[i:i + chunk * 2])
        time.sleep(pause)
