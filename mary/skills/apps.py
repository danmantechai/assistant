"""open_app / close_app."""
from __future__ import annotations

import time
from pathlib import Path

import psutil
from rapidfuzz import fuzz, utils

from mary import winutil
from mary.tools.registry import Tool, ToolContext, ToolResult

GUIDE = ('Opening and closing apps: open_app and close_app. "Open my browser" is open_app with name "browser"; '
         "a website or web app is web.")
GROUNDING = [
    # A small model sometimes "helps" with a tool nobody asked for: asked to check WhatsApp, it tried to
    # force-quit Edge. So closing only happens when the user's own words ask for it.
    ("close_app", lambda a: True,
     r"\b(close|quit|exit|kill|shut|terminate|get rid of)\b", "the user didn't ask to close anything"),
    ("close_app", lambda a: bool(a.get("force")),  # normally removed first by safety.ground_args()
     r"\b(force|kill|terminate)\b", "the user didn't ask to force-quit"),
    ("open_app", lambda a: True,
     r"\b(open|launch|start|run|fire up|bring up|load|boot|get|need|use|pull up|show|go to|switch to|play|check)\b",
     "the user didn't ask to open anything"),
]

_MATCH_CUTOFF = 75  # for running windows; titles are noisier than Start Menu names


def _running_targets(ctx: ToolContext, name: str) -> tuple[str, list[winutil.WindowInfo]]:
    """Find the open windows that belong to the app called `name`.

    Scores each window by its process image name and its title, and also accepts an exact
    hit on the .exe the Start Menu index associates with that app name.
    """
    query = ctx.apps.normalize(name)
    match = ctx.apps.match(name)
    index_exe = match.app.exe if match.app else None
    scored = []
    for w in winutil.list_windows():
        if winutil.is_own_console(w.hwnd):
            continue
        stem = Path(w.exe).stem
        score = max(fuzz.WRatio(query, stem, processor=utils.default_process),
                    fuzz.WRatio(query, w.title, processor=utils.default_process) - 5)
        if index_exe and w.exe == index_exe:
            score = 100
        if score >= _MATCH_CUTOFF:
            scored.append((score, w))
    if not scored:
        return "", []
    best_score, best = max(scored, key=lambda s: s[0])
    # Close every window of the winning process (or just the one, for shared hosts).
    if best.exe in ("applicationframehost.exe", "explorer.exe"):
        return best.title, [best]
    stem = Path(best.exe).stem
    # Prefer the Start Menu name ("Notepad") over the process name ("notepad").
    same_app = match.app and (match.app.exe == best.exe or fuzz.WRatio(match.app.name, stem) >= 90)
    label = match.app.name if same_app else stem
    return label, [w for s, w in scored if w.exe == best.exe and s >= best_score - 15]


def _wait_for_window(app, before: set[int], timeout: float = 6.0) -> winutil.WindowInfo | None:
    """Wait until the launched app has a window, then focus it, so a following "type …" lands in
    the new app rather than in whatever was in front before."""
    name = app.name.lower()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        fg = winutil.foreground_window()
        for w in winutil.list_windows():
            ours = (app.exe and w.exe == app.exe) or name in w.title.lower() or \
                fuzz.partial_ratio(name, w.title.lower()) >= 90
            if ours and (w.hwnd not in before or (fg and fg.hwnd == w.hwnd)):
                winutil.focus_window(w.hwnd)
                return w
        time.sleep(0.2)
    return None


def tools(ctx: ToolContext) -> list[Tool]:
    safety = ctx.cfg.safety

    sites = {k.lower(): v for k, v in (ctx.cfg.web.sites or {}).items()}

    def open_app(name: str) -> ToolResult:
        match = ctx.apps.match(name)
        if match.app is None:
            site = sites.get(ctx.apps.normalize(name).removesuffix(" web"))
            if site:  # not installed, but it's a web app: "open whatsapp" -> web.whatsapp.com
                from mary.skills.web import open_site

                reused = open_site(site)
                return ToolResult(True, f"{name} isn't installed, so {'switched to' if reused else 'opened'} "
                                        f"{site} in the browser.", say=f"Opening {name} in your browser.")
            if match.ambiguous:
                return ToolResult(False, f"'{name}' is ambiguous. Close matches: {', '.join(match.candidates)}. "
                                         "Ask the user which one.")
            hint = f" Closest: {', '.join(match.candidates)}." if match.candidates else ""
            return ToolResult(False, f"No installed app matches '{name}'.{hint}")
        before = {w.hwnd for w in winutil.list_windows()}
        ctx.apps.launch(match.app)
        window = _wait_for_window(match.app, before)
        if window:
            ctx.last_launch = (window.hwnd, time.monotonic())  # so typing waits until it's really ready
        ready = f" Its window is open and focused: {window.title}." if window else ""
        return ToolResult(True, f"Launched {match.app.name}.{ready}", say=f"Opening {match.app.name}.")

    def close_app(name: str, force: bool = False) -> ToolResult:
        label, windows = _running_targets(ctx, name)
        if not windows:
            return ToolResult(False, f"No open window matches '{name}'.")
        if force:
            for pid in {w.pid for w in windows}:
                psutil.Process(pid).terminate()
        else:
            for w in windows:
                winutil.close_window(w.hwnd)
        still_open = windows
        deadline = time.monotonic() + 2.0  # some apps take a moment to close
        while still_open and time.monotonic() < deadline:
            time.sleep(0.2)
            still_open = [w for w in still_open
                          if winutil.user32.IsWindow(w.hwnd) and winutil.user32.IsWindowVisible(w.hwnd)]
        if still_open and not force:
            return ToolResult(True, f"Asked {label} to close; it is still open, probably showing a save prompt.",
                              say=f"{label} wants to know about saving first.")
        return ToolResult(True, f"Closed {label}.", say=f"Closed {label}.")

    def close_tier(args: dict) -> int:
        if args.get("force"):
            return 2
        _, windows = _running_targets(ctx, args.get("name", ""))
        for w in windows:
            if w.exe in safety.editor_processes or any(m in w.title for m in safety.unsaved_title_markers):
                return 2
        return 1

    return [
        Tool("open_app", "Launch an installed application by name (Start Menu apps and Store apps).",
             {"name": {"type": "string", "description": "app name as the user said it, e.g. 'spotify'"}},
             ["name"], open_app, speaks_for_itself=True, announce=lambda a: f"Launching {a.get('name', 'app')}…"),
        Tool("close_app", "Close a running application's windows (politely, so it can ask to save).",
             {"name": {"type": "string", "description": "app name or window title"},
              "force": {"type": "boolean", "description": "kill the process; only if the user insists"}},
             ["name"], close_app, tier=close_tier, speaks_for_itself=True,
             announce=lambda a: f"Closing {a.get('name', 'app')}…",
             confirm_text=lambda a: f"{'force-quit' if a.get('force') else 'close'} {a.get('name')}, "
                                    "which may have unsaved work",
             ask=lambda a: (f"Force-quit {a.get('name')}? Anything unsaved will be lost." if a.get("force")
                            else f"{str(a.get('name', 'It')).capitalize()} may have unsaved work. Close it anyway?")),
    ]
