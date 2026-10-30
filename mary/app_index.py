"""Index of launchable apps, built from the Start Menu.

Two sources are merged:
  * `Get-StartApps`: every Start Menu entry including Microsoft Store apps (Spotify from
    the Store has no .lnk file, so a shortcut scan alone would miss it).
  * .lnk shortcuts in the user and all-users Start Menu folders: these give us the target
    .exe, which close_app uses to find the running process.
Apps launch through `shell:AppsFolder\\<AppID>`, which works for Store and desktop apps alike.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import threading
from dataclasses import asdict, dataclass
from pathlib import Path

from rapidfuzz import fuzz, process, utils

from mary.config import expand_path

log = logging.getLogger(__name__)

START_MENU_DIRS = [
    Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
    Path(os.environ.get("PROGRAMDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
]
_FILLER = re.compile(r"\b(the|my|app|application|program|window)\b")
# Windows' https handler ProgId -> Start Menu name of that browser.
_BROWSER_PROGIDS = {"msedgehtm": "Microsoft Edge", "chromehtml": "Google Chrome", "bravehtml": "Brave",
                    "operastable": "Opera", "firefoxurl": "Firefox", "vivaldihtm": "Vivaldi"}


def default_browser_name() -> str:
    import winreg

    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\Shell\Associations"
                                                       r"\UrlAssociations\https\UserChoice")
        prog_id = winreg.QueryValueEx(key, "ProgId")[0].lower()
    except OSError:
        return "Microsoft Edge"
    return next((name for prefix, name in _BROWSER_PROGIDS.items() if prog_id.startswith(prefix)),
                "Microsoft Edge")


@dataclass
class App:
    name: str
    app_id: str | None = None   # shell:AppsFolder id
    lnk: str | None = None      # shortcut path
    exe: str | None = None      # lower-case image name, e.g. "spotify.exe"


@dataclass
class AppMatch:
    app: App | None
    score: float
    candidates: list[str]       # close alternatives, for "did you mean" / the LLM

    @property
    def ambiguous(self) -> bool:
        return self.app is None and len(self.candidates) > 1


class AppIndex:
    def __init__(self, cfg):
        self.cache_path = expand_path(cfg.paths.data) / "app_index.json"
        self.aliases = {k.lower(): v for k, v in (cfg.apps.aliases or {}).items()}
        self.cutoff = cfg.apps.match_cutoff
        self.margin = cfg.apps.ambiguity_margin
        self.exclude = re.compile(cfg.apps.exclude_pattern)
        self._lock = threading.Lock()
        self.apps: list[App] = []
        self._load_cache()
        self.loaded_from_cache = bool(self.apps)
        if not self.apps:
            self.refresh()

    # ------------------------------------------------------------------ building

    def _load_cache(self) -> None:
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
            self.apps = [App(**a) for a in data]
        except (OSError, ValueError, TypeError):
            self.apps = []

    def refresh_in_background(self) -> None:
        threading.Thread(target=self.refresh, name="app-index", daemon=True).start()

    def refresh(self) -> None:
        try:
            apps = self._scan()
        except Exception:
            log.exception("app index refresh failed")
            return
        with self._lock:
            self.apps = apps
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps([asdict(a) for a in apps], indent=1), encoding="utf-8")
        log.info("app index: %d apps", len(apps))

    def _scan(self) -> list[App]:
        script = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
                  "Get-StartApps | Select-Object Name, AppID | ConvertTo-Json -Compress")
        out = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                             capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
        start_apps = json.loads(out.stdout.decode("utf-8") or "[]")
        if isinstance(start_apps, dict):
            start_apps = [start_apps]

        shortcuts = self._scan_shortcuts()
        apps: dict[str, App] = {}
        for entry in start_apps:
            name, app_id = (entry.get("Name") or "").strip(), entry.get("AppID") or ""
            if not name or self.exclude.search(name) or app_id.lower().startswith(("http:", "https:")):
                continue
            exe = Path(app_id).name.lower() if app_id.lower().endswith(".exe") else None
            lnk, lnk_exe = shortcuts.pop(name.lower(), (None, None))
            apps[name.lower()] = App(name=name, app_id=app_id, lnk=lnk, exe=exe or lnk_exe)
        for key, (lnk, exe) in shortcuts.items():  # shortcuts Get-StartApps didn't list
            name = Path(lnk).stem
            if key not in apps and not self.exclude.search(name):
                apps[key] = App(name=name, lnk=lnk, exe=exe)
        return sorted(apps.values(), key=lambda a: a.name.lower())

    @staticmethod
    def _scan_shortcuts() -> dict[str, tuple[str, str | None]]:
        from mary.winutil import ensure_com
        import win32com.client

        ensure_com()
        shell = win32com.client.Dispatch("WScript.Shell")
        found: dict[str, tuple[str, str | None]] = {}
        for root in START_MENU_DIRS:
            for lnk in root.rglob("*.lnk"):
                try:
                    target = shell.CreateShortcut(str(lnk)).TargetPath or ""
                except Exception:
                    target = ""
                exe = Path(target).name.lower() if target.lower().endswith(".exe") else None
                found.setdefault(lnk.stem.lower(), (str(lnk), exe))
        return found

    # ------------------------------------------------------------------ matching

    @staticmethod
    def normalize(name: str) -> str:
        return re.sub(r"\s+", " ", _FILLER.sub(" ", name.lower())).strip(" .,!?")

    def match(self, query: str) -> AppMatch:
        q = self.normalize(query)
        alias = self.aliases.get(q)
        if alias == "{default_browser}":
            alias = default_browser_name()
        if alias:
            if alias.lower().endswith(".exe"):  # alias straight to an executable
                return AppMatch(App(name=Path(alias).stem, exe=alias.lower()), 100, [])
            q = self.normalize(alias)
        with self._lock:
            apps = list(self.apps)
        if not q or not apps:
            return AppMatch(None, 0, [])

        results = process.extract(q, [a.name for a in apps], scorer=fuzz.WRatio,
                                  processor=utils.default_process, limit=5)
        candidates = [name for name, score, _ in results if score >= self.cutoff - 10]
        best_name, best_score, best_idx = results[0]
        exact = utils.default_process(best_name) == utils.default_process(q)
        heard = None if exact else self._sounds_like(q, apps, apps[best_idx])
        if heard is not None:
            return AppMatch(heard, best_score, [heard.name])
        if best_score < self.cutoff:
            return AppMatch(None, best_score, candidates)
        exact = utils.default_process(best_name) == utils.default_process(q)
        if not exact and len(results) > 1 and results[1][1] > best_score - self.margin:
            return AppMatch(None, best_score, candidates)  # too close to call
        return AppMatch(apps[best_idx], best_score, candidates)

    @staticmethod
    def _sounds_like(q: str, apps: list[App], fuzzy_winner: App) -> App | None:
        """Speech recognition splits names into words: 'notes part' -> Notepad, 'spot if I' -> Spotify.

        Compares whole names with spaces removed. Wins only if it is a clear winner AND sounds like
        the *whole* phrase much more than the ordinary fuzzy match does (which can latch onto one
        word: 'notes part' -> Sticky Notes)."""
        def squash(s: str) -> str:
            return re.sub(r"[^a-z0-9]", "", s.lower())

        compact = squash(q)
        if len(compact) < 4:
            return None
        scored = sorted(((fuzz.ratio(compact, squash(a.name)), a) for a in apps), key=lambda s: -s[0])
        best, app = scored[0]
        clear = len(scored) == 1 or best - scored[1][0] >= 8
        if best < 75 or not clear:
            return None
        if app is fuzzy_winner:  # both agree, even if the fuzzy score alone was too low to trust
            return app
        return app if best >= fuzz.ratio(compact, squash(fuzzy_winner.name)) + 15 else None

    # ------------------------------------------------------------------ launching

    @staticmethod
    def launch(app: App) -> None:
        if app.app_id:
            subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{app.app_id}"])
        elif app.lnk:
            os.startfile(app.lnk)
        else:
            os.startfile(app.exe)  # ShellExecute: finds PATH/App Paths, handles auto-elevation
