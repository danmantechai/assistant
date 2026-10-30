"""find_files (Windows Search index, with a folder-scan fallback) and file_action (open/move/recycle)."""
from __future__ import annotations

import logging
import os
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from rapidfuzz import fuzz

from mary import winutil
from mary.brain import mode as brain_mode
from mary.config import expand_path
from mary.parsers import parse_time_window
from mary.reflexes import Reflex
from mary.tools.registry import Tool, ToolContext, ToolResult

GUIDE = ("Files on this PC (documents, PDFs, pictures, downloads): find_files to look for them, then file_action "
         'to open, move or recycle one, by its number in the list. "Open a picture" means a picture file on this '
         "PC (find_files with file_type image), never a web search. Refer to files by name.")
GROUNDING = [
    ("file_action", lambda a: a.get("action") == "move",
     r"\b(move|put|transfer)\b", "the user didn't ask to move a file"),
    ("file_action", lambda a: a.get("action") == "recycle",
     r"\b(delete|remove|trash|recycle|bin|get rid of|erase|throw)\b", "the user didn't ask to delete a file"),
    ("undo_move", lambda a: True, r"\b(undo|back)\b", "the user didn't ask to undo a move"),
]
SLOW = {"find_files": ["Let me have a look.", "Let me look through your files."]}

# New mode: moving a file he named just happens, and "undo" moves it back for a few minutes afterwards (only
# then, so "undo" in a text editor still means Ctrl+Z there).
UNDO_S = 600
_last_move = {"at": -UNDO_S, "from": "", "to": ""}
REFLEXES = [
    Reflex(r"undo(?: (?:that|it|the move|moving it))?|move it back|put it back", "undo_move",
           lambda text: {} if time.monotonic() - _last_move["at"] < UNDO_S else None, first=True),
]

log = logging.getLogger(__name__)

FILE_TYPES = {
    "pdf": [".pdf"],
    "document": [".pdf", ".doc", ".docx", ".odt", ".rtf", ".txt", ".md"],
    "word": [".doc", ".docx"],
    "spreadsheet": [".xls", ".xlsx", ".xlsm", ".csv", ".ods"],
    "excel": [".xls", ".xlsx", ".xlsm", ".csv"],
    "presentation": [".ppt", ".pptx", ".odp"],
    "powerpoint": [".ppt", ".pptx"],
    "slides": [".ppt", ".pptx", ".odp"],
    "image": [".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".bmp", ".tif", ".tiff"],
    "photo": [".jpg", ".jpeg", ".png", ".heic", ".webp"],
    "picture": [".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".bmp"],
    "screenshot": [".png", ".jpg"],
    "video": [".mp4", ".mkv", ".mov", ".avi", ".webm", ".wmv"],
    "audio": [".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".wma"],
    "music": [".mp3", ".flac", ".m4a", ".aac", ".ogg", ".wav"],
    "archive": [".zip", ".rar", ".7z", ".tar", ".gz"],
    "zip": [".zip"],
    "installer": [".exe", ".msi"],
    "text": [".txt", ".md", ".log"],
}
# Words that are part of asking, never part of a file's name. Without this, "can you give me a list of
# images in my downloads" searched for images *named* "give me list of have" and found none.
GENERIC_WORDS = {
    "a", "an", "the", "my", "me", "i", "i'm", "im", "i've", "ive", "you", "your", "we", "our", "it", "its",
    "them", "they", "these", "those", "that", "this", "there", "there's", "theres", "what", "what's", "whats",
    "which", "where", "where's", "wheres", "who", "how", "many", "much", "is", "are", "was", "were", "be",
    "been", "do", "does", "did", "can", "could", "would", "will", "should", "please", "kindly", "just", "only",
    "also", "again", "now", "currently", "right", "ok", "okay", "so", "if", "whether", "any", "some", "all",
    "every", "each", "new", "newest", "latest", "recent", "recently", "last", "lately", "old", "oldest",
    "give", "show", "tell", "list", "lists", "listing", "check", "see", "look", "looking", "find", "found",
    "search", "searching", "locate", "get", "got", "have", "has", "had", "let", "know", "want", "need", "like",
    "mean", "help", "able", "file", "files", "folder", "folders", "directory", "stuff", "thing", "things",
    "everything", "anything", "something", "item", "items", "content", "contents", "one", "ones", "kind",
    "kinds", "type", "types", "sort", "available", "exist", "exists", "still", "yet", "ever", "about", "of",
    "in", "inside", "into", "on", "at", "from", "for", "to", "with", "by", "and", "or", "downloaded",
    "download", "downloads", "saved", "save", "stored", "made", "created", "received", "put", "keep", "kept",
    "computer", "pc", "laptop", "drive", "called", "named", "titled", "name",
}
_STOPWORDS = GENERIC_WORDS | {"document", "documents", "doc", "docs"}
_FOLDERS = {"desktop": "Desktop", "documents": "Documents", "docs": "Documents", "downloads": "Downloads",
            "download": "Downloads", "pictures": "Pictures", "photos": "Pictures", "music": "Music",
            "videos": "Videos"}


def _extensions(file_type: str | None) -> list[str]:
    if not file_type:
        return []
    t = file_type.strip().lower().lstrip(".")
    t = FILE_TYPES.get(t) or FILE_TYPES.get(t.rstrip("s"))
    if t:
        return t
    return ["." + file_type.strip().lower().lstrip(".")]  # a literal extension like "psd"


def _resolve_folder(folder: str | None, defaults: list[Path]) -> list[Path]:
    if not folder:
        return defaults
    key = folder.strip().lower()
    if key in _FOLDERS:
        return [winutil.known_folder(_FOLDERS[key])]
    p = Path(os.path.expandvars(os.path.expanduser(folder)))
    return [p] if p.is_dir() else defaults


def _terms(query: str | None, exts: list[str]) -> list[str]:
    """Name words to match. A quoted query ("\"list\"", from "the file called list") is taken literally."""
    quoted = re.fullmatch(r'\s*"([^"]+)"\s*', query or "")
    if quoted:
        return [quoted.group(1).lower()]
    words = re.findall(r"[\w'.-]+", (query or "").lower())
    drop = _STOPWORDS | {e.lstrip(".") for e in exts} | set(FILE_TYPES)
    return [w for w in words if w not in drop and w.rstrip("s") not in drop]


def _search_index(folders: list[Path], terms: list[str], exts: list[str], since: datetime | None,
                  limit: int) -> list[str] | None:
    """Query the Windows Search index. Returns None if the index is unavailable."""
    try:
        winutil.ensure_com()
        import win32com.client

        conn = win32com.client.Dispatch("ADODB.Connection")
        conn.Open("Provider=Search.CollatorDSO;Extended Properties='Application=Windows';")
        esc = lambda s: s.replace("'", "''")
        where = ["(" + " OR ".join(f"SCOPE='file:{esc(p.as_posix())}'" for p in folders) + ")"]
        if exts:
            where.append("(" + " OR ".join(f"System.FileExtension = '{e}'" for e in exts) + ")")
        if since:
            utc = since.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            where.append(f"(System.DateModified >= '{utc}' OR System.DateCreated >= '{utc}')")
        for t in terms:
            where.append(f"System.FileName LIKE '%{esc(t)}%'")
        sql = (f"SELECT TOP {limit * 3} System.ItemPathDisplay FROM SystemIndex WHERE "
               f"{' AND '.join(where)} ORDER BY System.DateModified DESC")
        rs = win32com.client.Dispatch("ADODB.Recordset")
        rs.Open(sql, conn)
        paths = []
        while not rs.EOF:
            paths.append(rs.Fields.Item("System.ItemPathDisplay").Value)
            rs.MoveNext()
        rs.Close()
        conn.Close()
        return paths
    except Exception as e:
        log.warning("Windows Search query failed, falling back to folder scan: %s", e)
        return None


def _walk(folders: list[Path], terms: list[str], exts: list[str], since: datetime | None,
          budget_s: float, skip: set[str]) -> list[str]:
    """Plain folder scan, newest first; stops when the time budget runs out."""
    deadline = time.monotonic() + budget_s
    cutoff = since.timestamp() if since else 0
    found = []
    for root in folders:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in skip and not d.startswith(".")]
            for name in filenames:
                low = name.lower()
                if exts and not low.endswith(tuple(exts)):
                    continue
                if terms and not all(t in low for t in terms) and \
                        fuzz.partial_ratio(" ".join(terms), low) < 85:
                    continue
                path = os.path.join(dirpath, name)
                try:
                    st = os.stat(path)
                except OSError:
                    continue
                if max(st.st_mtime, st.st_ctime) >= cutoff:
                    found.append(path)
            if time.monotonic() > deadline:
                log.info("folder scan hit its time budget")
                return found
    return found


def _describe(path: str, roots: list[Path]) -> str:
    """Compact one-liner for the LLM: every token here costs ~30 ms of CPU prompt processing."""
    p = Path(path)
    st = p.stat()
    when = datetime.fromtimestamp(max(st.st_mtime, st.st_ctime)).strftime("%a %d %b")
    size = st.st_size
    size_text = f"{size / 2**20:.1f} MB" if size >= 2**20 else f"{max(1, size // 1024)} KB"
    folder = p.parent.name
    for root in roots:
        if p.is_relative_to(root):
            folder = root.name if p.parent == root else f"{root.name}/…/{p.parent.name}"
            break
    return f"{p.name} | {folder} | {when} | {size_text}"


_SPOKEN_TYPE = {"pdf": "PDF", "excel": "Excel", "word": "Word", "powerpoint": "PowerPoint"}


def _kind(file_type: str | None) -> str:
    """'pdf' -> 'PDF ', 'images' -> 'image ', None -> '' (for "I found 3 PDF files")."""
    t = (file_type or "").strip().lower().lstrip(".")
    if t and t not in FILE_TYPES and t[:-1] in FILE_TYPES:
        t = t[:-1]
    return f"{_SPOKEN_TYPE.get(t, t.upper() if t not in FILE_TYPES else t)} " if t else ""


def _when(modified_within: str | None) -> str:
    """' from today' for the reply; nothing for phrasings that don't read well after 'from'."""
    m = (modified_within or "").strip().lower()
    return f" from {m}" if re.fullmatch(r"today|yesterday|(this|last) (week|month|year)", m) else ""


def _spoken_name(path: str, type_known: bool) -> str:
    """How to say a file's name: without the extension when the type was already said ("3 PDF files")."""
    p = Path(path)
    return p.stem if type_known else p.name


def _and(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def tools(ctx: ToolContext) -> list[Tool]:
    new_mode = brain_mode(ctx.cfg) == "new"

    fcfg = ctx.cfg.files
    defaults = [expand_path(p) for p in ctx.cfg.paths.search_folders]

    def find_files(query: str = "", folder: str | None = None, file_type: str | None = None,
                   modified_within: str | None = None) -> ToolResult:
        folders = _resolve_folder(folder, defaults)
        exts = _extensions(file_type)
        if not exts:  # "find my PDFs" with the type only in the query
            for word in re.findall(r"\w+", (query or "").lower()):
                if word in FILE_TYPES or word.rstrip("s") in FILE_TYPES:
                    exts, file_type = _extensions(word), word
                    break
        since = parse_time_window(modified_within)
        terms = _terms(query, exts)
        paths = _search_index(folders, terms, exts, since, fcfg.max_results)
        source = "index"
        if not paths:  # index unavailable, folder not indexed, or genuinely nothing: scan to be sure
            paths = _walk(folders, terms, exts, since, fcfg.walk_time_budget_s, set(fcfg.skip_dirs))
            source = "scan"
        existing = [p for p in dict.fromkeys(paths) if os.path.isfile(p)]
        existing.sort(key=lambda p: max(os.stat(p).st_mtime, os.stat(p).st_ctime), reverse=True)
        existing = existing[:fcfg.max_results]
        ctx.last_files, ctx.last_files_at = existing, time.monotonic()
        where = ", ".join(f.name for f in folders)
        kind = _kind(file_type)
        if not existing:
            since_text = f" since {since:%a %d %b}" if since else ""
            named = f" matching \"{' '.join(terms)}\"" if terms else ""
            return ToolResult(True, f"No matching files in {where}{since_text}.",
                              say=f"I couldn't find any {kind}files{named} in {where}{_when(modified_within)}.")
        shown = existing[:fcfg.show_results]
        lines = [f"{i}. {_describe(p, folders)}" for i, p in enumerate(shown, 1)]
        more = f"\n(+{len(existing) - len(shown)} older matches)" if len(existing) > len(shown) else ""
        log.info("find_files via %s: %d results", source, len(existing))
        names = [_spoken_name(p, bool(kind)) for p in existing[:3]]
        if len(existing) == 1:
            say = f"I found one {kind}file in {where}: {names[0]}."
        elif len(existing) <= 3:
            say = f"I found {len(existing)} {kind}files in {where}: {_and(names)}."
        else:
            count = f"at least {len(existing)}" if len(existing) == fcfg.max_results else str(len(existing))
            say = f"I found {count} {kind}files in {where}. The newest are {_and(names)}."
        return ToolResult(True, f"{len(existing)} {kind}file(s) in {where}, newest first:\n"
                                + "\n".join(lines) + more, say=say)

    def _resolve_path(path: str) -> Path | None:
        p = path.strip().strip("'\"").lstrip("#")
        if p.isdigit() and 1 <= int(p) <= len(ctx.last_files):
            return Path(ctx.last_files[int(p) - 1])
        if p.lower() in _FOLDERS:  # "downloads": the folder itself (opens in File Explorer)
            return winutil.known_folder(_FOLDERS[p.lower()])
        candidate = Path(os.path.expandvars(os.path.expanduser(p)))
        if candidate.exists():
            return candidate
        # The path as find_files shows it ("Pictures/…/MARY/x.png") or as she reads it back from that
        # ("Pictures/MARY/x.png"): the 2B was told twice that such a path didn't exist.
        parts = [q for q in candidate.parts if q not in ("…", "...")]
        if len(parts) > 1 and parts[0].lower() in _FOLDERS:  # under the known folder
            under = winutil.known_folder(_FOLDERS[parts[0].lower()]).joinpath(*parts[1:])
            if under.exists():
                return under
        named = [Path(f) for f in ctx.last_files if Path(f).name.lower() == candidate.name.lower()]
        if len(named) > 1:  # the same name in two folders: the one in the folders she gave
            folders = {q.lower() for q in parts[:-1]}
            named = [f for f in named if folders <= {q.lower() for q in f.parts}] or named
        return named[0] if named else None

    def file_action(action: str, path: str, destination: str | None = None) -> ToolResult:
        target = _resolve_path(path)
        if target is None:
            return ToolResult(False, f"Can't find '{path}'. Use find_files first.")
        if action != "open" and target.is_dir():  # never a whole folder (e.g. "downloads") to the Recycle Bin
            return ToolResult(False, f"{target.name} is a folder; only single files can be moved or recycled.",
                              say="I only move or recycle single files, not whole folders.")
        if action == "open":
            os.startfile(target)
            return ToolResult(True, f"Opened {target}.", say=f"Opening {target.name}.")
        if action == "recycle":
            from send2trash import send2trash

            send2trash(str(target))
            if new_mode:
                return ToolResult(True, f"Moved {target.name} to the Recycle Bin; he can restore it from there.",
                                  say="It's in the Recycle Bin if you need it back.")
            return ToolResult(True, f"Moved {target} to the Recycle Bin.", say="It's in the Recycle Bin.")
        if action == "move":
            if not destination:
                return ToolResult(False, "move needs a destination folder.")
            dest = _resolve_folder(destination, [])
            if not dest:
                return ToolResult(False, f"Destination folder '{destination}' doesn't exist.")
            new_path = dest[0] / target.name
            if new_path.exists():
                return ToolResult(False, f"{new_path} already exists; not overwriting it.")
            shutil.move(str(target), str(new_path))
            _last_move.update({"at": time.monotonic(), "from": str(target), "to": str(new_path)})
            if new_mode:
                return ToolResult(True, f"Moved {target.name} to {dest[0].name}. He can say undo to move it back.",
                                  say=f"Moved it to {dest[0].name}. Say undo to move it back.")
            return ToolResult(True, f"Moved to {new_path}.", say=f"Moved it to {dest[0].name}.")
        return ToolResult(False, "action must be open, move or recycle.")

    def undo_move() -> ToolResult:
        moved, back = Path(_last_move["to"]), Path(_last_move["from"])
        if time.monotonic() - _last_move["at"] >= UNDO_S or not moved.exists():
            return ToolResult(False, "There's no recent move to undo.", say="There's nothing to undo.")
        if back.exists():
            return ToolResult(False, f"{back} exists again, so the file wasn't moved back.",
                              say=f"There's already a {back.name} where it came from, so I left it.")
        shutil.move(str(moved), str(back))
        _last_move["at"] = -UNDO_S
        return ToolResult(True, f"Moved {moved.name} back to {back.parent.name}.", say=f"Done, it's back in {back.parent.name}.")

    def _name(a: dict) -> str:
        p = _resolve_path(a.get("path", ""))
        return p.name if p else a.get("path", "the file")

    return [
        Tool("find_files",
             "Find files by name/type/date in the user's Desktop, Documents and Downloads "
             "(or a given folder). Newest first.",
             {"query": {"type": "string", "description": "words from the file name; may be empty"},
              "folder": {"type": "string", "description": "desktop, documents, downloads, pictures, music, "
                                                          "videos, or a full path"},
              "file_type": {"type": "string", "description": "pdf, document, spreadsheet, presentation, image, "
                                                             "video, audio, archive, or an extension"},
              "modified_within": {"type": "string", "description": "e.g. today, yesterday, this week, 3 days, "
                                                                   "this month"}},
             [], find_files, speaks_for_itself=True, announce=lambda a: "Searching files…"),
        Tool("file_action",
             "Open a file, move it to another folder, or send it to the Recycle Bin (never permanent delete).",
             {"action": {"type": "string", "enum": ["open", "move", "recycle"]},
              "path": {"type": "string", "description": "full path, a result as the last find_files showed it, "
                                                        "or that result's number"},
              "destination": {"type": "string", "description": "for move: folder name or path"}},
             ["action", "path"], file_action, speaks_for_itself=True,
             tier=lambda a: 1 if a.get("action") == "open" else 2,
             new_tier=1,  # a move can be undone, and the Recycle Bin gives a file back
             announce=lambda a: f"{a.get('action', '').capitalize()} {_name(a)}…",
             confirm_text=lambda a: (f"move {_name(a)} to {a.get('destination')}" if a.get("action") == "move"
                                     else f"send {_name(a)} to the Recycle Bin")),
    ] + ([Tool("undo_move", "Move the file MARY just moved back where it was.", {}, [], undo_move,
               announce=lambda a: "Moving it back…")] if new_mode else [])
