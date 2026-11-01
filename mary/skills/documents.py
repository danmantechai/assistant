"""Brick: read a document's text by code (read_document), so "what does my statement say" never means opening it to
read the screen, or PowerShell on a PDF (in the replay she tried Get-Content on one).

It reads PDFs (pypdf), Word, Excel and PowerPoint files (their XML, with Python's own zip and XML readers), and
anything that is text: .txt, Markdown, CSV, logs, HTML (without its tags). What a file is comes from its first bytes,
not only its name. Others get a sentence saying so and what can read them instead: a picture or a scanned PDF (no
text layer) is for look (mary/vision.py), an old .doc or an .rtf is opened and read from the screen.

A document is a list of pages: a PDF's pages, a deck's slides, a workbook's sheets, a Word document's pages as Word
last laid them out, and a big text file's parts of a MB (a small one is one page). Only as much is read as the answer
needs, and it always fits the room a tool result has (llm.tool_result_chars):
  - the whole of a short document, with "[page 2]" where each page starts;
  - the start and the end of a long one, with "… n lines skipped (pages 2–6) …" between; the pages in the middle
    are never read, so a 900-page PDF takes as long as a short one;
  - only the pages asked for (pages="2-4", "last"), or only the lines with the words asked for (find), under the
    line that names the columns when the file has one (CSV, a sheet).
Tables keep their columns: "a | b" for Word and Excel, a PDF's own layout squeezed to the width of its columns.
"""
from __future__ import annotations

import codecs
import logging
import os
import re
import time
import unicodedata
import zipfile
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable
from xml.etree import ElementTree as ET

from mary import winutil
from mary.tools.registry import Tool, ToolContext, ToolResult

GUIDE = ("What a document or file says (a statement, letter, bill, report: PDF, Word, Excel, PowerPoint, text or CSV): "
         "find_files, then read_document with its number. Never open it to read the screen, and never PowerShell on "
         "it. For more of a long one, read_document again with pages or find.")
SLOW = {"read_document": ["Let me read it.", "One moment, I'm reading it."]}

log = logging.getLogger(__name__)

LINE_CHARS = 200            # a longer line is cut, with "…"
WHOLE_TEXT = 2 * 2**20      # a text file up to this is one page; a bigger one is read in parts of PART bytes
PART = 2**20
FIND_SECONDS = 8.0          # looking for words in a huge document stops here, and says how far it got
MAX_ROWS = 20000            # rows read from one sheet
_CACHE = 4                  # documents kept read (a follow-up with pages or find reads the same one again)

_FOLDERS = {"desktop": "Desktop", "documents": "Documents", "docs": "Documents", "downloads": "Downloads",
            "download": "Downloads", "pictures": "Pictures", "photos": "Pictures", "music": "Music",
            "videos": "Videos"}

# What isn't a document with text, by extension: (what it is, what can read it instead).
_SCREEN = "Open it (file_action) and read_screen can read it from the screen."
_OTHER = {
    **dict.fromkeys(".jpg .jpeg .png .gif .webp .heic .bmp .tif .tiff .svg .ico".split(),
                    ("a picture", "Open it (file_action) and look can say what it shows.")),
    **dict.fromkeys(".mp4 .mkv .mov .avi .webm .wmv".split(), ("a video", "")),
    **dict.fromkeys(".mp3 .wav .flac .m4a .aac .ogg .wma".split(), ("an audio file", "")),
    **dict.fromkeys(".zip .rar .7z .tar .gz".split(), ("an archive of other files", "Open it (file_action) to see them.")),
    **dict.fromkeys(".exe .msi .dll .lnk".split(), ("a program", "")),
    ".doc": ("an old Word document (.doc)", _SCREEN), ".xls": ("an old Excel workbook (.xls)", _SCREEN),
    ".ppt": ("an old PowerPoint deck (.ppt)", _SCREEN), ".rtf": ("a rich-text document (.rtf)", _SCREEN),
    **dict.fromkeys(".odt .ods .odp".split(), ("an OpenDocument file", _SCREEN)),
    ".epub": ("an e-book", _SCREEN), ".one": ("a OneNote section", _SCREEN), ".msg": ("an Outlook message", _SCREEN),
}
_OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"   # old Office files, and new ones locked with a password
_OFFICE = {".docx", ".docm", ".dotx", ".xlsx", ".xlsm", ".xltx", ".pptx", ".pptm", ".ppsx"}

# Ligatures, invisible marks and odd spaces that PDFs and Word use; out, so "finance" finds "ﬁnance".
_TIDY = str.maketrans({"\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl", "\ufb03": "ffi", "\ufb04": "ffl",
                       "\ufb05": "st", "\ufb06": "st", "\xa0": " ", "\u202f": " ", "\u2007": " ", "\xad": None,
                       "\u200b": None, "\u200c": None, "\u200d": None, "\ufeff": None, "\t": "  "})
_SMALL_WORDS = {"a", "an", "the", "of", "in", "on", "at", "to", "for", "and", "or", "my", "me", "is", "are", "was",
                "what", "how", "much", "many", "with", "from", "by", "it", "its", "this", "that", "any", "all"}


class Unreadable(Exception):
    """Why a file can't be read, as a sentence for the model that says what can read it instead."""


@dataclass
class Doc:
    """A document as pages: `page(i)` gives page i's lines (1-based), read only when asked for."""
    kind: str                                   # "PDF", "Word document", …
    count: int                                  # pages, slides, sheets or parts
    page: Callable[[int], list[str]]
    unit: str = "page"                          # "page", "slide", "sheet", "part"; "" for a one-page text file
    names: list[str] = field(default_factory=list)       # sheet names
    heads: dict[int, str] = field(default_factory=dict)  # the line naming the columns, by page (0: every page)
    pictures: set[int] = field(default_factory=set)      # pages that are a picture with no text (scanned)
    about: str = ""                             # more for the first line ("as Word last laid it out")
    keep: bool = True                           # keep pages read (not a huge text file's parts)
    _read: dict[int, list[str]] = field(default_factory=dict)

    def lines(self, i: int) -> list[str]:
        if i in self._read:
            return self._read[i]
        lines = _clean(self.page(i))
        if self.keep:
            self._read[i] = lines
        return lines

    def marker(self, i: int) -> str | None:
        """'[page 2]' where a page starts ('[sheet 2: Spending]'); nothing in a document of one page."""
        if not self.unit or self.count < 2:
            return None
        name = self.names[i - 1] if i <= len(self.names) else ""
        return f"[{self.unit} {i}{': ' + name if name else ''}]"

    def head(self, i: int) -> str | None:
        return self.heads.get(i, self.heads.get(0))


def _clean(lines: list[str]) -> list[str]:
    """Odd characters out, trailing spaces off, long lines cut, runs of blank lines down to one, none at the ends."""
    out: list[str] = []
    for line in lines:
        line = (line if line.isascii() and "\t" not in line else line.translate(_TIDY)).rstrip()
        line = line if len(line) <= LINE_CHARS else line[:LINE_CHARS - 1] + "…"
        if line or (out and out[-1]):
            out.append(line)
    while out and not out[-1]:
        out.pop()
    return out


# ------------------------------------------------------------------ finding the file

def resolve(ctx: ToolContext, path: str) -> Path:
    """The file meant: a full path, a result number from the last find_files, or a result as find_files showed it
    ("Documents/…/statement.pdf", or its name). Raises Unreadable with what to do instead."""
    p = str(path).split(" | ")[0].strip().strip("'\"").lstrip("#").strip()   # a whole line as find_files shows it
    number = re.fullmatch(r"(\d+)(?:[.)]\s+.*)?", p)                         # "2", or "2. Statement.pdf"
    if number:
        n = int(number.group(1))
        if 1 <= n <= len(ctx.last_files):
            return _file(Path(ctx.last_files[n - 1]))
        raise Unreadable(f"There's no result {n} from find_files. Use find_files first.")
    candidate = Path(os.path.expandvars(os.path.expanduser(p)))
    if candidate.is_absolute() and candidate.exists():
        return _file(candidate)
    parts = [q for q in candidate.parts if q not in ("…", "...")]
    if len(parts) > 1 and parts[0].lower() in _FOLDERS:   # "Documents/Bank/x.pdf", under the real folder
        under = winutil.known_folder(_FOLDERS[parts[0].lower()]).joinpath(*parts[1:])
        if under.exists():
            return _file(under)
    named = [Path(f) for f in ctx.last_files if Path(f).name.lower() == candidate.name.lower()]
    if named:                                              # a name from the last find_files
        return _file(named[0])
    raise Unreadable(f"Can't find '{path}'. Use find_files first.")


def _file(p: Path) -> Path:
    if p.is_dir():
        raise Unreadable(f"{p.name} is a folder, not a document; find_files lists what's in it.")
    return p


# ------------------------------------------------------------------ what it is

def open_doc(path: Path) -> Doc:
    """Read what kind of document it is from its first bytes (and its name), ready to read page by page."""
    try:
        with open(path, "rb") as f:
            start = f.read(8192)
    except OSError as e:
        raise Unreadable(f"{path.name} couldn't be opened: {e.strerror or e}.")
    ext = path.suffix.lower()
    if not start:
        raise Unreadable(f"{path.name} is empty.")
    if b"%PDF-" in start[:1024]:
        return _pdf(path)
    if start.startswith(b"PK\x03\x04"):
        return _zipped(path, ext)
    if start.startswith(_OLE):
        if ext in _OFFICE:
            raise Unreadable(f"{path.name} is locked with a password, so its text can't be read. "
                             "Open it (file_action) and read_screen can read it once it's unlocked.")
        what, then = _OTHER.get(ext, ("an old Office file", _SCREEN))
        raise Unreadable(f"{path.name} is {what}, which read_document can't read. {then}")
    if ext in _OTHER or start.startswith(b"{\\rtf"):
        what, then = _OTHER.get(ext, _OTHER[".rtf"])
        raise Unreadable(f"{path.name} is {what}, not a document read_document can read. {then}".strip())
    return _text(path, start, ext)


def _zipped(path: Path, ext: str) -> Doc:
    try:
        z = zipfile.ZipFile(path)
        names = set(z.namelist())
    except (zipfile.BadZipFile, OSError) as e:
        raise Unreadable(f"{path.name} couldn't be read: {e}. It may be damaged.")
    if "word/document.xml" in names:
        return _word(z)
    if "xl/workbook.xml" in names:
        return _excel(z)
    if "ppt/presentation.xml" in names:
        return _slides(z)
    if "mimetype" in names and b"opendocument" in z.read("mimetype"):
        ext = ".odt"
    what, then = _OTHER.get(ext, _OTHER[".zip"])
    raise Unreadable(f"{path.name} is {what}, not a document read_document can read. {then}".strip())


# ------------------------------------------------------------------ PDF

def _pdf(path: Path) -> Doc:
    try:
        from pypdf import PdfReader
    except ImportError:
        raise Unreadable("Reading PDFs needs pypdf, which isn't installed (pip install -r requirements.txt). "
                         + _SCREEN)
    try:
        reader = PdfReader(path)
        if reader.is_encrypted and not reader.decrypt(""):
            raise Unreadable(f"{path.name} is locked with a password, so its text can't be read. "
                             "Open it (file_action) and read_screen can read it once it's unlocked.")
        count = len(reader.pages)
        if not count:
            raise Unreadable(f"{path.name} has no pages that can be read. It may be damaged.")
    except Unreadable:
        raise
    except Exception as e:  # pypdf's errors for damaged files, and a missing decryption library
        raise Unreadable(f"{path.name} couldn't be read as a PDF ({e}). It may be damaged.")
    doc = Doc("PDF", count, lambda i: [])

    def page(i: int) -> list[str]:
        p = reader.pages[i - 1]
        try:
            text = p.extract_text(extraction_mode="layout")   # keeps a table's columns apart
        except Exception:
            try:
                text = p.extract_text()
            except Exception as e:
                log.info("page %d of %s has no readable text: %s", i, path.name, e)
                text = ""
        lines = squeeze(text.splitlines())
        if not any(line.strip() for line in lines) and _has_picture(p):
            doc.pictures.add(i)
        return lines

    doc.page = page
    return doc


def _has_picture(page) -> bool:
    """Does the page draw a picture (a scan), directly or inside a form?"""
    def obj(x):
        return x.get_object() if hasattr(x, "get_object") else x

    def look(resources, depth: int) -> bool:
        try:
            for ref in (obj(obj(resources or {}).get("/XObject")) or {}).values():
                x = obj(ref)
                if x.get("/Subtype") == "/Image":
                    return True
                if x.get("/Subtype") == "/Form" and depth < 3 and look(x.get("/Resources"), depth + 1):
                    return True
        except Exception:
            return False
        return False
    return look(page.get("/Resources"), 0)


def squeeze(lines: list[str]) -> list[str]:
    """A PDF's layout text, with its columns kept but the space between them squeezed: in each run of lines that
    look like a table (gaps of 3+ spaces), the character columns blank in every line become at most two spaces.
    "Netflix          72.88" and "SALARY      3,150.00" stay in their columns (paid out, paid in)."""
    out: list[str] = []
    block: list[str] = []
    for line in lines + [""]:
        if re.search(r"\S {3,}\S", line):
            block.append(line.rstrip())
            continue
        if block:
            out += _columns(block)
            block = []
        out.append(line.strip())
    return out[:-1]


def _columns(block: list[str]) -> list[str]:
    width = max(map(len, block))
    rows = [line.ljust(width) for line in block]
    blank = [all(r[c] == " " for r in rows) for c in range(width)]
    keep: list[int] = []
    c = 0
    while c < width:
        if not blank[c]:
            keep.append(c)
            c += 1
            continue
        end = c
        while end < width and blank[end]:
            end += 1
        if c > 0 and end < width:     # inside: at most two; at either edge: none
            keep += range(c, c + min(end - c, 2))
        c = end
    return ["".join(r[k] for k in keep).rstrip() for r in rows]


# ------------------------------------------------------------------ Word, Excel, PowerPoint

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def _xml(z: zipfile.ZipFile, name: str) -> ET.Element | None:
    try:
        return ET.fromstring(z.read(name))
    except (KeyError, ET.ParseError):
        return None


def _rels(z: zipfile.ZipFile, part: str) -> dict[str, str]:
    """A part's relationships: id -> the zip path it points to."""
    folder, name = part.rsplit("/", 1)
    root = _xml(z, f"{folder}/_rels/{name}.rels")
    out = {}
    for rel in root if root is not None else []:
        target = rel.get("Target") or ""
        if rel.get("TargetMode") == "External":
            continue
        path = target.lstrip("/") if target.startswith("/") else os.path.normpath(f"{folder}/{target}")
        out[rel.get("Id")] = path.replace("\\", "/")
    return out


def _word(z: zipfile.ZipFile) -> Doc:
    """Paragraphs in order, headings as "# Title", list items as "- item", table rows as "a | b | c". Pages are
    where Word last broke them (its rendered page breaks) and where the writer put a page break."""
    root = _xml(z, "word/document.xml")
    body = root.find(f"{_W}body") if root is not None else None
    if body is None:
        raise Unreadable("This Word document couldn't be read; it may be damaged.")
    levels = _heading_levels(z)
    pages: list[list[str]] = [[]]

    def add(text: str, before: bool, after: bool) -> None:
        if before and any(pages[-1]):
            pages.append([])
        pages[-1].append(text)
        if after:
            pages.append([])

    def walk(parent: ET.Element) -> None:
        for el in parent:
            if el.tag == f"{_W}p":
                style = el.find(f"{_W}pPr/{_W}pStyle")
                level = levels.get(style.get(f"{_W}val") if style is not None else "", 0)
                text, before, after = _paragraph(el)
                if el.find(f"{_W}pPr/{_W}pageBreakBefore") is not None:
                    before = True
                if text and level:
                    text = "#" * level + " " + text
                elif text and el.find(f"{_W}pPr/{_W}numPr") is not None:
                    text = "- " + text
                add(text, before, after)
            elif el.tag == f"{_W}tbl":
                for row in el.iter(f"{_W}tr"):
                    cells = [" / ".join(t for t in (_paragraph(p)[0] for p in cell.iter(f"{_W}p")) if t)
                             for cell in row.findall(f"{_W}tc")]
                    breaks = [_paragraph(p) for p in row.iter(f"{_W}p")]
                    add(" | ".join(cells).rstrip(" |"), any(b for _, b, _ in breaks[:1]), any(a for _, _, a in breaks))
                add("", False, False)
            elif el.tag != f"{_W}sectPr":
                walk(el)   # content controls, custom XML and the like hold paragraphs too

    walk(body)
    pages = [p for p in pages if any(p)] or [[]]
    laid_out = len(pages) > 1
    return Doc("Word document", len(pages), lambda i: pages[i - 1], unit="page" if laid_out else "",
               about="as Word last laid it out" if laid_out else "")


def _paragraph(p: ET.Element) -> tuple[str, bool, bool]:
    """A paragraph's text, and whether a page starts before it or after it."""
    parts: list[str] = []
    before = after = False
    for el in p.iter():
        tag = el.tag
        if tag == f"{_W}t":
            parts.append(el.text or "")
        elif tag in (f"{_W}tab", f"{_W}ptab"):
            parts.append(" ")
        elif tag in (f"{_W}br", f"{_W}cr"):
            if el.get(f"{_W}type") == "page":
                before, after = (True, after) if not "".join(parts).strip() else (before, True)
            else:
                parts.append(" ")
        elif tag == f"{_W}noBreakHyphen":
            parts.append("-")
        elif tag == f"{_W}lastRenderedPageBreak":
            if "".join(parts).strip():
                after = True   # the page broke inside it: the next one starts after it
            else:
                before = True
    return re.sub(r"\s+", " ", "".join(parts)).strip(), before, after


def _heading_levels(z: zipfile.ZipFile) -> dict[str, int]:
    """Style id -> heading level, from the styles' names ("heading 1", "Title") or outline levels; the ids are
    translated in other languages ("berschrift1"), the names aren't."""
    root = _xml(z, "word/styles.xml")
    levels: dict[str, int] = {}
    for style in root.iter(f"{_W}style") if root is not None else []:
        sid = style.get(f"{_W}styleId") or ""
        name_el, outline = style.find(f"{_W}name"), style.find(f"{_W}pPr/{_W}outlineLvl")
        name = (name_el.get(f"{_W}val") if name_el is not None else "").lower()
        m = re.fullmatch(r"heading (\d)", name)
        if name == "title":
            levels[sid] = 1
        elif m:
            levels[sid] = int(m.group(1))
        elif outline is not None and (outline.get(f"{_W}val") or "9").isdigit() and int(outline.get(f"{_W}val")) < 9:
            levels[sid] = int(outline.get(f"{_W}val")) + 1
    return levels


def _excel(z: zipfile.ZipFile) -> Doc:
    """Each sheet a page, each row "a | b | c" with empty cells kept, so columns stay in place; dates as dates."""
    book = _xml(z, "xl/workbook.xml")
    if book is None:
        raise Unreadable("This Excel workbook couldn't be read; it may be damaged.")
    rels = _rels(z, "xl/workbook.xml")
    sheets = [(s.get("name") or f"Sheet{i}", rels.get(s.get(f"{_R}id") or "", ""))
              for i, s in enumerate(book.iter(f"{_S}sheet"), 1)]
    if not sheets:
        raise Unreadable("This Excel workbook has no sheets.")
    pr = book.find(f"{_S}workbookPr")
    epoch = datetime(1904, 1, 1) if pr is not None and pr.get("date1904") in ("1", "true") else datetime(1899, 12, 30)
    strings = _xml(z, "xl/sharedStrings.xml")
    shared = [_text_of(si, exclude=f"{_S}rPh") for si in (strings if strings is not None else [])]
    dates = _date_styles(z)
    doc = Doc("Excel workbook", len(sheets), lambda i: [], unit="sheet", names=[n for n, _ in sheets])

    def page(i: int) -> list[str]:
        part = sheets[i - 1][1]
        if part not in z.namelist():
            return ["(a chart, no cells)"] if "chartsheet" in part else []
        lines: list[str] = []
        first = 0   # the sheet's first column ("B2:F70" starts at B)
        with z.open(part) as f:
            for _, el in ET.iterparse(f):
                if el.tag == f"{_S}dimension":
                    first = _column(el.get("ref") or "") or 0
                if el.tag != f"{_S}row":
                    continue
                if len(lines) >= MAX_ROWS:
                    lines.append(f"… only the first {MAX_ROWS} rows were read …")
                    break
                cells: dict[int, str] = {}
                for c in el.iter(f"{_S}c"):
                    col = _column(c.get("r") or "")
                    cells[col if col is not None else max(cells, default=first - 1) + 1] = _cell(c, shared, dates,
                                                                                                 epoch)
                el.clear()
                row = (" | ".join(cells.get(k, "") for k in range(min(first, min(cells)), max(cells) + 1))
                       .rstrip(" |") if cells else "")
                if row.strip(" |"):
                    if not lines and i not in doc.heads:
                        doc.heads[i] = row
                    lines.append(row)
        return lines

    doc.page = page
    return doc


def _text_of(el: ET.Element, exclude: str = "") -> str:
    skip = {id(t) for x in el.iter(exclude) for t in x.iter()} if exclude else set()
    return "".join(t.text or "" for t in el.iter() if t.tag.endswith("}t") and id(t) not in skip)


def _column(ref: str) -> int | None:
    letters = re.match(r"[A-Z]+", ref)
    if not letters:
        return None
    n = 0
    for ch in letters.group():
        n = n * 26 + ord(ch) - 64
    return n - 1


def _date_styles(z: zipfile.ZipFile) -> set[int]:
    """Indexes of the cell styles that show a number as a date or time."""
    root = _xml(z, "xl/styles.xml")
    if root is None:
        return set()
    custom = {}
    for f in root.iter(f"{_S}numFmt"):
        code = re.sub(r'"[^"]*"|\[[^\]]*\]|\\.', "", f.get("formatCode") or "").lower()
        custom[int(f.get("numFmtId") or 0)] = bool(re.search(r"[dmyhs]", code))
    xfs = root.find(f"{_S}cellXfs")
    out = set()
    for i, xf in enumerate(xfs if xfs is not None else []):
        fmt = int(xf.get("numFmtId") or 0)
        if 14 <= fmt <= 22 or 45 <= fmt <= 47 or custom.get(fmt):
            out.add(i)
    return out


def _cell(c: ET.Element, shared: list[str], dates: set[int], epoch: datetime) -> str:
    kind, v = c.get("t"), c.find(f"{_S}v")
    value = v.text if v is not None and v.text is not None else ""
    if kind == "s":
        return shared[int(value)] if value.isdigit() and int(value) < len(shared) else ""
    if kind == "inlineStr":
        return _text_of(c)
    if kind == "b":
        return "TRUE" if value == "1" else "FALSE"
    if kind in ("str", "e") or not value:
        return value
    try:
        number = float(value)
    except ValueError:
        return value
    if int(c.get("s") or 0) in dates and 0 <= number < 2958466:
        when = epoch + timedelta(days=number)
        return f"{when:%Y-%m-%d}" if number == int(number) else f"{when:%Y-%m-%d %H:%M}"
    return f"{number:.15g}"   # as Excel shows it: 15 digits at most (72.880000000000003 is 72.88)


def _slides(z: zipfile.ZipFile) -> Doc:
    """Each slide a page: its text boxes' paragraphs in order, a table's rows as "a | b"."""
    pres = _xml(z, "ppt/presentation.xml")
    rels = _rels(z, "ppt/presentation.xml")
    order = [rels.get(s.get(f"{_R}id") or "", "") for s in pres.iter(f"{_P}sldId")] if pres is not None else []
    order = [p for p in order if p in z.namelist()]
    if not order:
        raise Unreadable("This PowerPoint deck has no slides.")

    def page(i: int) -> list[str]:
        root = _xml(z, order[i - 1])
        lines: list[str] = []

        def walk(el: ET.Element) -> None:
            for child in el:
                if child.tag == f"{_A}tr":
                    lines.append(" | ".join(" / ".join(_text_of(p) for p in tc.iter(f"{_A}p"))
                                            for tc in child.findall(f"{_A}tc")).rstrip(" |"))
                elif child.tag == f"{_A}p":
                    lines.append(_text_of(child).strip())
                else:
                    walk(child)
        if root is not None:
            walk(root)
        return lines

    return Doc("PowerPoint deck", len(order), page, unit="slide")


# ------------------------------------------------------------------ text

def _text(path: Path, start: bytes, ext: str) -> Doc:
    """Text in whatever encoding it's in; a big file in parts of PART bytes, each read only when asked for."""
    codec, skip = _encoding(start)
    if codec is None:
        raise Unreadable(f"{path.name} isn't a document read_document can read (it isn't text). {_SCREEN}")
    size = path.stat().st_size
    html = ext in (".htm", ".html", ".xhtml") or start.lstrip()[:15].lower().startswith((b"<!doctype html", b"<html"))
    table = ext in (".csv", ".tsv")
    kind = "HTML page" if html else "CSV file" if table else "Markdown file" if ext == ".md" else "text file"
    if size <= WHOLE_TEXT:
        text = path.read_bytes()[skip:].decode(codec, "replace")
        lines = _html_lines(text) if html else text.splitlines()
        doc = Doc(kind, 1, lambda i: lines, unit="")
    else:
        nl = "\n".encode(codec)
        count = -(-(size - skip) // PART)
        doc = Doc(kind, count, lambda i: _part(path, i, skip, size, codec, nl).splitlines(), unit="part",
                  about=f"{_size(size)}, read in parts of {_size(PART)}", keep=False)
    if table:
        first = next((line for line in doc.lines(1) if line.strip()), None)
        if first:
            doc.heads[0] = first
    return doc


def _size(n: int) -> str:
    return f"{n / 2**20:,.0f} MB" if n >= 2**20 else f"{max(1, round(n / 1024))} KB"


def _encoding(start: bytes) -> tuple[str | None, int]:
    """(codec, BOM length); no codec when it isn't text."""
    for bom, codec in ((codecs.BOM_UTF8, "utf-8"), (codecs.BOM_UTF16_LE, "utf-16-le"),
                       (codecs.BOM_UTF16_BE, "utf-16-be")):
        if start.startswith(bom):
            return codec, len(bom)
    if len(start) >= 4 and start[1::2].count(0) > len(start) // 4 and start[0::2].count(0) == 0:
        return "utf-16-le", 0   # Windows' "Unicode" text without its mark
    if 0 in start:
        return None, 0
    try:
        start.decode("utf-8")
        return "utf-8", 0
    except UnicodeDecodeError as e:
        return ("utf-8", 0) if e.start >= len(start) - 3 else ("cp1252", 0)   # cut mid-character at the end, or ANSI


def _part(path: Path, i: int, skip: int, size: int, codec: str, nl: bytes) -> str:
    """Part i of a big text file: the lines that start inside its PART bytes."""
    unit = len(nl)
    start, end = skip + (i - 1) * PART, min(size, skip + i * PART)
    origin = max(skip, start - unit)
    with open(path, "rb") as f:
        f.seek(origin)
        data = f.read(end - origin + 64 * 1024)

    def newline(at: int) -> int:
        k = data.find(nl, max(0, at))
        while k >= 0 and k % unit:
            k = data.find(nl, k + 1)
        return k

    first = 0 if i == 1 else newline(0) + unit if newline(0) >= 0 else len(data)
    last = newline(end - origin - unit)
    return data[first:last if last >= 0 else len(data)].decode(codec, "replace")


def _html_lines(text: str) -> list[str]:
    """A web page's text: no tags, scripts or styles; a line per block."""
    from html.parser import HTMLParser

    blocks = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "table", "section", "article",
              "header", "footer", "ul", "ol", "title", "pre", "blockquote"}
    out: list[str] = []

    class Strip(HTMLParser):
        hidden = 0
        line = ""

        def handle_starttag(self, tag, attrs):
            if tag in ("script", "style", "noscript", "template"):
                self.hidden += 1
            elif tag in ("td", "th") and self.line.strip():
                self.line += " | "
            elif tag in blocks:
                self.flush()

        def handle_endtag(self, tag):
            if tag in ("script", "style", "noscript", "template"):
                self.hidden = max(0, self.hidden - 1)
            elif tag in blocks:
                self.flush()

        def handle_data(self, data):
            if not self.hidden:
                self.line += data

        def flush(self):
            text = re.sub(r"\s+", " ", self.line).strip()
            if text:
                out.append(text)
            self.line = ""

    parser = Strip(convert_charrefs=True)
    parser.feed(text)
    parser.close()
    parser.flush()
    return out


# ------------------------------------------------------------------ choosing what to show

_docs: OrderedDict[tuple, Doc] = OrderedDict()


def load(path: Path) -> Doc:
    """The document, read once while it's unchanged (follow-ups with pages or find ask for it again)."""
    st = path.stat()
    key = (str(path).lower(), st.st_mtime_ns, st.st_size)
    if key in _docs:
        _docs.move_to_end(key)
        return _docs[key]
    doc = open_doc(path)
    _docs[key] = doc
    while len(_docs) > _CACHE:
        _docs.popitem(last=False)
    return doc


def which_pages(spec, count: int, unit: str = "page") -> list[int]:
    """'2', '2-4', '1, 3 and 5-6', 'last', 'last 2', 'first 3', '3 to end' -> page numbers, in order. Raises
    Unreadable when it names none it has."""
    text = str(spec).lower().replace("–", "-").replace("—", "-").strip("[]() ")
    text = re.sub(r"\b(pages?|slides?|sheets?|parts?|and)\b", ",", text)
    text = re.sub(r"\s*(?:-|\bto\b|\bthrough\b)\s*", "-", text)
    text = re.sub(r"\b(?:end|last)\b(?!\s*\d)", str(count), text)
    chosen: list[int] = []
    for token in filter(None, (t.strip() for t in re.split(r"[,;]", text))):
        m = re.fullmatch(r"(first|last)\s*(\d+)", token)
        if m:
            n = min(int(m.group(2)), count)
            chosen += range(1, n + 1) if m.group(1) == "first" else range(count - n + 1, count + 1)
            continue
        m = re.fullmatch(r"(\d+)\s*(?:-\s*(\d+))?", token)
        if not m:
            raise Unreadable(f"pages should be like '2', '3-5' or 'last', not '{spec}'.")
        a, b = int(m.group(1)), int(m.group(2) or m.group(1))
        chosen += range(min(a, b), max(a, b) + 1)
    inside = sorted({n for n in chosen if 1 <= n <= count})
    if not inside:
        asked = min(chosen) if chosen else 0
        raise Unreadable(f"It has {count} {unit}{'s' if count != 1 else ''}, so there's no {unit} {asked}.")
    return inside


_THOUSANDS = re.compile(r"(?<=\d)[,'](?=\d{3}\b)")
_NOT_WORD = re.compile(r"[\W_]+")


def _norm(s: str) -> str:
    """For comparing: same case, ligatures and odd spaces out, no thousands commas (3,150 = 3150). Quick for the
    plain lines most documents are made of: a long one is searched line by line."""
    if not s.isascii():
        s = unicodedata.normalize("NFKC", s.translate(_TIDY))
    s = s.casefold()
    return _THOUSANDS.sub("", s) if "," in s or "'" in s else s


def _compact(s: str) -> str:
    return _NOT_WORD.sub("", s)


@dataclass
class Shown:
    lines: list[str]
    read: list[int]         # the pages that were read
    note: str = ""          # how to read on
    found: str = ""         # what was found ("12 lines with 'netflix'")


Item = tuple[int, int, str]   # (page, its line number, the line); line number 0 for a page's marker


def whole(doc: Doc, chosen: list[int], room: float) -> Shown:
    """The pages in order; too long for the room, their start and their end with what was skipped between, and the
    call that reads the skipped part. The pages in between are never read."""
    front: list[Item] = []
    size, k = 0, 0
    while k < len(chosen) and size <= room:
        front += _with_marker(doc, chosen[k])
        size = sum(len(line) + 1 for _, _, line in front)
        k += 1
    if size <= room and k == len(chosen):
        return Shown([line for _, _, line in front], chosen)
    back: list[Item] = []
    j = len(chosen)
    while j > k and sum(len(line) + 1 for _, _, line in back) < room * 0.25:
        j -= 1
        back[:0] = _with_marker(doc, chosen[j])
    unread = chosen[k:j]
    if not unread:          # all of it read: one run of lines
        front, back = front + back, []
    head = _fit(front, room * 0.7)
    rest = front[len(head):]
    source = back or rest
    tail = _fit(source[::-1], room * 0.25)[::-1]
    gap = rest + back[:len(back) - len(tail)] if back else rest[:len(rest) - len(tail)]
    gap_pages = sorted({i for i, _, _ in gap} | set(unread))
    before, after = head[-1][0] if head else None, tail[0][0] if tail else None
    span = _gap(doc, gap_pages, before, after)
    if unread:
        skipped = f"… {span} skipped …"
    else:
        lines = sum(1 for _, n, line in gap if n and line)
        skipped = f"… {lines} lines skipped{f' ({span})' if span else ''} …"
    out = [line for _, _, line in head] + [skipped]
    if tail:
        out += _context(doc, tail[0][0], tail[0][2]) + [line for _, _, line in tail]
    page, line = next(((i, n) for i, n, _ in gap if n), (gap_pages[0], 1))
    return Shown(out, chosen[:k] + chosen[j:],
                 note=f"To read what was skipped: read_document again with {_onward(doc, gap_pages, page, line)}, "
                      "or with find='words'.")


def onward(doc: Doc, chosen: list[int], line: int, room: float) -> Shown:
    """The pages asked for, read on from a line of the first one, as far as the room goes; then the call that reads
    on from there. Reading on like this reaches every line of a document, however long its pages."""
    lines = len(_with_marker(doc, chosen[0])) - (1 if doc.marker(chosen[0]) else 0)
    if line > lines:
        where = _span(doc, chosen[:1]) or "It"
        raise Unreadable(f"{where[0].upper()}{where[1:]} has {lines} line{'s' if lines != 1 else ''}, so there's "
                         f"no line {line}.")
    out: list[str] = []
    used, shown = 0, 0
    for k, i in enumerate(chosen):
        items = [it for it in _with_marker(doc, i) if it[1] >= (line if k == 0 else 1)]
        context = _context(doc, i, items[0][2]) if items else []
        for x, (page, n, text) in enumerate(items):
            cost = sum(len(c) + 1 for c in context) + len(text) + 1
            if shown and used + cost > room:
                return Shown(out, chosen[:k + 1], note=f"To read on: read_document again with "
                                                       f"{_onward(doc, chosen[k:], page, n or 1)}.")
            out += context + [text]
            used += cost
            shown += 1
            context = []
    return Shown(out, chosen)


def _onward(doc: Doc, pages: list[int], page: int, line: int) -> str:
    """The arguments that read on from a line of a page: "pages='2-3', from_line=14"."""
    rest = [i for i in pages if i >= page] or [page]
    args = [f"pages='{_range(rest)}'"] if doc.unit and doc.count > 1 else []
    return ", ".join(args + [f"from_line={line}"])


def _with_marker(doc: Doc, i: int) -> list[Item]:
    marker = doc.marker(i)
    lines = doc.lines(i)   # reading the page is what finds out it's a picture
    if i in doc.pictures:
        lines = ["(a picture with no text: look can read it)"]
    return ([(i, 0, marker)] if marker else []) + [(i, n, line) for n, line in enumerate(lines, 1)]


def _fit(items: list[Item], room: float) -> list[Item]:
    out, used = [], 0
    for item in items:
        if used + len(item[2]) + 1 > room:
            break
        out.append(item)
        used += len(item[2]) + 1
    return out


def _context(doc: Doc, i: int, first: str) -> list[str]:
    """Before lines shown away from their page's start: the page's marker, and the line naming the columns."""
    out = []
    marker, head = doc.marker(i), doc.head(i)
    if marker and first != marker:
        out.append(marker)
    if head and first not in (head, marker):
        out.append(head)
    return out


def _span(doc: Doc, pages: list[int]) -> str:
    """'pages 2–5', 'pages 1, 3 and 7–9', 'slide 4'; nothing for a document without pages."""
    if not doc.unit or doc.count < 2 or not pages:
        return ""
    runs = _runs(pages, "–")
    listed = runs[0] if len(runs) == 1 else ", ".join(runs[:-1]) + " and " + runs[-1]
    return f"{doc.unit}{'s' if len(pages) > 1 else ''} {listed}"


def _runs(pages: list[int], dash: str) -> list[str]:
    runs, start = [], pages[0]
    for a, b in zip(pages, pages[1:] + [None]):
        if b != a + 1:
            runs.append(str(a) if start == a else f"{start}{dash}{a}")
            start = b
    return runs


def _gap(doc: Doc, pages: list[int], before: int | None, after: int | None) -> str:
    """What was skipped: 'the rest of page 1, pages 2–399 and the start of page 400', 'the middle of page 2'."""
    if not doc.unit or doc.count < 2:
        return ""
    if before == after and pages == [before]:
        return f"the middle of {doc.unit} {before}"
    parts = [f"the rest of {doc.unit} {before}"] if before in pages else []
    middle = [i for i in pages if i not in (before, after)]
    parts += [_span(doc, middle)] if middle else []
    parts += [f"the start of {doc.unit} {after}"] if after in pages and after != before else []
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


def _range(pages: list[int]) -> str:
    """The pages argument that asks for them: '4', '4-9', '1, 3-5'."""
    return ", ".join(_runs(sorted(pages), "-"))


def find_in(doc: Doc, chosen: list[int], words: str, room: float, seconds: float | None = None) -> Shown:
    """Only the lines with the words asked for, under their page's marker (and the line naming the columns). All the
    words first; else as many of them as any line has; else lines that nearly match (a misheard or misspelt word)."""
    from rapidfuzz import fuzz

    query = _norm(words).strip()
    terms = [w for w in (t.strip(".,") for t in query.split()) if w and w not in _SMALL_WORDS] or query.split()
    compact = _compact(query)
    near_enough = 75 if len(compact) <= 5 else 80   # one letter wrong ("eror", "salery", "Netflics")
    if not terms:
        raise Unreadable("find needs a word or two to look for.")
    every: list[tuple[int, str]] = []
    some: list[tuple[int, str]] = []    # the lines with the most of the words (best of them)
    best = 0
    near: list[tuple[int, str]] = []
    deadline = time.perf_counter() + (FIND_SECONDS if seconds is None else seconds)
    searched: list[int] = []
    for i in chosen:
        if searched and time.perf_counter() > deadline:
            break
        searched.append(i)
        for line in doc.lines(i):
            n = _norm(line)
            hits = sum(t in n for t in terms)
            if hits == len(terms):
                every.append((i, line))
            elif hits and not every:
                if hits > best:
                    some, best = [], hits
                if hits == best:
                    some.append((i, line))
            elif not hits and not every and not some and len(compact) >= 4 and \
                    fuzz.partial_ratio(compact, _compact(n), score_cutoff=near_enough):
                near.append((i, line))
    quoted = f"'{words.strip()}'"
    if every:
        found, how = every, f"with {quoted}"
    elif some:
        found, how = some, f"with some of {quoted} (none has all of it)"
    else:
        found, how = near, f"close to {quoted} (none has it exactly)"
    unsearched = [i for i in chosen if i not in searched]
    note = (f"Only {_span(doc, searched)} could be searched in the time; read_document again with "
            f"pages='{_range(unsearched)}' to search on.") if unsearched else ""
    if not found:
        pictured = [i for i in searched if i in doc.pictures]
        also = f" ({_span(doc, pictured) or 'It'} is a picture with no text; look can read it.)" if pictured else ""
        return Shown([], searched, note=note, found=f"no line has {quoted}.{also}")
    lines: list[str] = []
    used, page, shown = 0, None, 0
    for i, line in found:
        extra = _context(doc, i, line) if i != page else []
        cost = sum(len(x) + 1 for x in extra + [line])
        if used + cost > room:
            break
        lines += extra + [line]
        used += cost
        shown, page = shown + 1, i
    if shown < len(found):
        span = _span(doc, sorted({i for i, _ in found[shown:]}))
        lines.append(f"… {len(found) - shown} more{f' ({span})' if span else ''}: narrow it with pages or more words …")
    return Shown(lines, searched, note=note, found=f"{len(found)} line{'s' if len(found) != 1 else ''} {how}:")


def read(path: Path, pages=None, find: str | None = None, room: int = 1500, from_line=None) -> ToolResult:
    """The document's text for the model, within `room` characters."""
    try:
        doc = load(path)
        everything = list(range(1, doc.count + 1))
        asked = pages not in (None, "") and str(pages).strip().lower() not in ("all", "every page", "all pages")
        chosen = which_pages(pages, doc.count, doc.unit or "page") if asked and doc.unit else everything
        size = f"{doc.count} {doc.unit}{'s' if doc.count != 1 else ''}" if doc.unit else ""
        names = f": {', '.join(doc.names)}" if doc.names and len(", ".join(doc.names)) < 150 else ""
        title = f"{path.name} ({', '.join(x for x in (doc.kind, size + names, doc.about) if x)})"
        if chosen != everything:
            title += f", {_span(doc, chosen)}"
        start = _line_number(from_line)
        if start and start > 1 and not (find and str(find).strip()):
            title += f", from line {start}"
        budget = max(LINE_CHARS + 10, room - len(title) - 150)   # the first line, and the one saying how to read on
        if find and str(find).strip():
            shown = find_in(doc, chosen, str(find), budget)
        elif start:
            shown = onward(doc, chosen, start, budget)
        else:
            shown = whole(doc, chosen, budget)
    except Unreadable as e:
        return ToolResult(False, str(e), say=str(e))
    except Exception as e:
        log.warning("couldn't read %s: %s", path.name, e)
        return ToolResult(False, f"{path.name} couldn't be read: {e}.", say=f"I couldn't read {path.name}.")
    if not find and not any(i not in doc.pictures and doc.lines(i) for i in shown.read):
        if any(i in doc.pictures for i in shown.read):
            return ToolResult(True, f"{title}: it's scanned, so its pages are pictures with no text to read. Open "
                                    "it (file_action) and look can read it from the screen.",
                              say="It's a scan, so there's no text in it to read.")
        return ToolResult(True, f"{title}: there's no text in it.", say=f"There's no text in {path.stem}.")
    if asked and not doc.unit:
        shown.note = (shown.note + f" (A {doc.kind} has no pages: this is all of it.)").strip()
    lines = [f"{title}: {shown.found}" if shown.found else f"{title}:", *shown.lines]
    note = [shown.note] if shown.note else []
    while len(lines) > 1 and len("\n".join(lines + note)) > room:   # a room too small: never lose the way on
        lines.pop()
    text = "\n".join(lines + note)
    if len(text) > room:  # never left for the agent to cut off without saying
        text = text[:room - 1] + "…"
    return ToolResult(True, text, say=f"Here's what {path.stem} says.")


def _line_number(value) -> int | None:
    if value in (None, ""):
        return None
    try:
        n = int(str(value).strip())
    except ValueError:
        raise Unreadable(f"from_line should be a line number, not '{value}'.")
    return max(1, n)


def tools(ctx: ToolContext) -> list[Tool]:
    def read_document(path: str, pages: str | None = None, find: str | None = None,
                      from_line: int | None = None) -> ToolResult:
        try:
            target = resolve(ctx, path)
        except Unreadable as e:
            return ToolResult(False, str(e), say=str(e))
        return read(target, pages, find, ctx.cfg.llm.tool_result_chars, from_line)

    def _name(a: dict) -> str:
        try:
            return resolve(ctx, a.get("path", "")).name
        except Unreadable:
            return "the document"

    return [Tool(
        "read_document",
        "Read a document's text: PDF, Word, Excel, PowerPoint, text, Markdown or CSV. A long one shows its start and "
        "its end, and says how to read what was skipped: pages, from_line, or find.",
        {"path": {"type": "string", "description": "a result's number from the last find_files, or a full path"},
         "pages": {"type": "string", "description": "pages (slides, sheets) to read, e.g. '2', '3-5' or 'last'; "
                                                    "omit for the whole document"},
         "from_line": {"type": "integer", "description": "read on from this line of the first page asked for"},
         "find": {"type": "string", "description": "words to look for: only the lines with them are shown"}},
        ["path"], read_document, private=True, watch=False, announce=lambda a: f"Reading {_name(a)}…")]
