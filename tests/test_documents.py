"""read_document (mary/skills/documents.py) on documents made here in a temporary folder: PDFs written by hand (text
pages, pages that are only a picture, a locked one, a damaged one, 400 pages), Word, Excel and PowerPoint files with
the quirks real ones have (runs split mid-word, tracked changes, translated style names, merged cells, dates, slides
out of file order), text in the encodings Windows writes, huge files, files that aren't documents, and paths that are
wrong. Plus a statement printed to PDF by Edge (tests/fixtures/statement.pdf), whose fonts and layout are a real
browser's, not ours."""
import re
import time
import zipfile
from datetime import date
from pathlib import Path

import pytest

from mary.app_index import AppIndex
from mary.config import load_config
from mary.output import Output
from mary.skills import documents
from mary.skills.documents import read, squeeze, which_pages
from mary.tools.registry import ToolContext, ToolRegistry

FIXTURE = Path(__file__).parent / "fixtures" / "statement.pdf"


# ------------------------------------------------------------------ making documents

def make_pdf(path: Path, pages: list[list[str] | None]) -> Path:
    """A real PDF written by hand: each page its lines of text (Helvetica), or None for a page that is only a
    picture, as a scanner makes them."""
    objects: list[bytes] = [b"", b"", b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
                                      b"/Encoding /WinAnsiEncoding >>"]

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    def stream(head: str, data: bytes) -> bytes:
        return f"<< {head} /Length {len(data)} >>\nstream\n".encode() + data + b"\nendstream"

    image = add(stream("/Type /XObject /Subtype /Image /Width 8 /Height 8 /ColorSpace /DeviceGray "
                       "/BitsPerComponent 8", bytes(range(0, 256, 4))))
    kids = []
    for lines in pages:
        if lines is None:
            content, resources = b"q 500 0 0 700 50 50 cm /Im0 Do Q", f"/XObject << /Im0 {image} 0 R >>"
        else:
            esc = [line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") for line in lines]
            content = "\n".join(["BT /F1 10 Tf 12 TL 40 800 Td"] + [f"({line}) Tj T*" for line in esc] + ["ET"]) \
                .encode("cp1252")
            resources = "/Font << /F1 3 0 R >>"
        body = add(stream("", content))
        kids.append(add(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents {body} 0 R "
                        f"/Resources << {resources} >> >>".encode()))
    objects[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>".encode()
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for n, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(bytes(out))
    return path


def make_zip(path: Path, parts: dict[str, str]) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in parts.items():
            z.writestr(name, text)
    return path


XML = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
W = ('xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
     'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"')
REL = 'xmlns="http://schemas.openxmlformats.org/package/2006/relationships"'
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def p(*runs: str, style: str = "", extra: str = "") -> str:
    """A paragraph of runs; a run is its text, or raw XML when it starts with '<'."""
    ppr = f'<w:pPr><w:pStyle w:val="{style}"/>{extra}</w:pPr>' if style else (f"<w:pPr>{extra}</w:pPr>" if extra else "")
    body = "".join(r if r.startswith("<") else f'<w:r><w:t xml:space="preserve">{r}</w:t></w:r>' for r in runs)
    return f"<w:p>{ppr}{body}</w:p>"


def cell(*paras: str, merged: bool = False) -> str:
    pr = "<w:tcPr><w:vMerge/></w:tcPr>" if merged else ""
    return f"<w:tc>{pr}{''.join(p(x) for x in paras) or '<w:p/>'}</w:tc>"


# Word as it writes a German user's styles: the ids are translated, the names stay English.
STYLES = (XML + f'<w:styles {W}>'
          '<w:style w:type="paragraph" w:styleId="Titel"><w:name w:val="Title"/></w:style>'
          '<w:style w:type="paragraph" w:styleId="berschrift1"><w:name w:val="heading 1"/></w:style>'
          '<w:style w:type="paragraph" w:styleId="berschrift2"><w:name w:val="heading 2"/></w:style>'
          '<w:style w:type="paragraph" w:styleId="Wichtig"><w:name w:val="Wichtig"/>'
          '<w:pPr><w:outlineLvl w:val="2"/></w:pPr></w:style>'
          '<w:style w:type="paragraph" w:styleId="Standard"><w:name w:val="Normal"/></w:style></w:styles>')


def make_docx(path: Path, body: str) -> Path:
    return make_zip(path, {
        "[Content_Types].xml": XML + '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                               '<Default Extension="xml" ContentType="application/xml"/></Types>',
        "_rels/.rels": XML + f'<Relationships {REL}><Relationship Id="rId1" Type="{R}/officeDocument" '
                             'Target="word/document.xml"/></Relationships>',
        "word/document.xml": XML + f"<w:document {W}><w:body>{body}<w:sectPr/></w:body></w:document>",
        "word/styles.xml": STYLES})


LEASE = "".join([
    p("Tenancy ", "Agree", "ment", style="Titel"),
    p("Rent", style="berschrift1"),
    p("Payment", style="berschrift2"),
    # a run split mid-number, a tab, a tracked deletion and insertion, a field, a hyperlink
    p("The rent is £1,2", "50 a month", "<w:r><w:tab/></w:r>", "due on the 1st ",
      "<w:del><w:r><w:delText>£1,100 </w:delText></w:r></w:del>", "<w:ins><w:r><w:t>(updated)</w:t></w:r></w:ins>"),
    p("Page ", '<w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText> PAGE </w:instrText></w:r>'
               '<w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t>1</w:t></w:r>'
               '<w:r><w:fldChar w:fldCharType="end"/></w:r>'),
    p("See ", '<w:hyperlink r:id="rId9"><w:r><w:t>example.org</w:t></w:r></w:hyperlink>', " for details."),
    p("Deposit: five weeks", extra='<w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr>'),
    p("Keep pets out of the garden", style="Wichtig"),
    "<w:tbl><w:tr>" + cell("Date") + cell("Amount") + cell("Paid") + "</w:tr>"
    "<w:tr>" + cell("1 Oct") + cell("£1,250") + cell("Yes", "by card") + "</w:tr>"
    "<w:tr>" + cell(merged=True) + cell("£50") + cell("No") + "</w:tr></w:tbl>",
    '<w:sdt><w:sdtContent>' + p("Signed: Sam Example") + '</w:sdtContent></w:sdt>',
    p('<w:r><w:br w:type="page"/></w:r>'),
    p("Schedule of condition", style="berschrift1"),
    p("Walls freshly painted."),
    p("<w:r><w:lastRenderedPageBreak/><w:t>Inventory: one sofa.</w:t></w:r>"),
])


def make_xlsx(path: Path) -> Path:
    s = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    serial = (date(2026, 10, 2) - date(1899, 12, 30)).days
    sheet1 = (XML + f'<worksheet {s}><dimension ref="B2:F4"/><sheetData>'
              '<row r="2"><c r="B2" t="s"><v>0</v></c><c r="C2" t="s"><v>1</v></c><c r="D2" t="s"><v>2</v></c>'
              '<c r="E2" t="s"><v>3</v></c><c r="F2" t="inlineStr"><is><t>Cleared</t></is></c></row>'
              f'<row r="3"><c r="B3" s="1"><v>{serial}</v></c><c r="C3" t="s"><v>4</v></c>'
              '<c r="E3"><v>3150</v></c><c r="F3" t="b"><v>1</v></c></row>'          # D3 empty: paid in, not out
              f'<row r="4"><c r="B4" s="2"><v>{serial + 1}.5</v></c><c r="C4" t="s"><v>5</v></c>'
              '<c r="D4"><v>72.880000000000003</v></c><c r="F4" t="str"><f>IF(1,"no")</f><v>no</v></c></row>'
              '</sheetData></worksheet>')
    sheet2 = (XML + f'<worksheet {s}><sheetData><row r="1"><c r="A1" t="s"><v>6</v></c>'
              '<c r="B1"><v>0.15</v></c></row></sheetData></worksheet>')
    strings = ["Date", "Description", "Paid out", "Paid in", "SALARY ACME WIDGETS", "Netflix", "Savings rate"]
    shared = "".join(f"<si><t>{t}</t></si>" for t in strings[:-1]) + \
        '<si><r><t>Savings </t></r><r><t>rate</t></r><rPh><t>セービング</t></rPh></si>'   # rich text, phonetic hint
    return make_zip(path, {
        "xl/workbook.xml": XML + f'<workbook {s} xmlns:r="{R}"><sheets>'
                                 '<sheet name="Transactions" sheetId="1" r:id="rId1"/>'
                                 '<sheet name="Rates" sheetId="2" r:id="rId2"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": XML + f'<Relationships {REL}>'
                                            f'<Relationship Id="rId1" Type="{R}/worksheet" Target="worksheets/sheet1.xml"/>'
                                            f'<Relationship Id="rId2" Type="{R}/worksheet" Target="/xl/worksheets/sheet2.xml"/>'
                                            '</Relationships>',
        "xl/sharedStrings.xml": XML + f"<sst {s}>{shared}</sst>",
        "xl/styles.xml": XML + f'<styleSheet {s}><numFmts><numFmt numFmtId="164" formatCode="dd/mm/yyyy\\ hh:mm"/>'
                               '</numFmts><cellXfs><xf numFmtId="0"/><xf numFmtId="14"/><xf numFmtId="164"/>'
                               '</cellXfs></styleSheet>',
        "xl/worksheets/sheet1.xml": sheet1, "xl/worksheets/sheet2.xml": sheet2})


def make_pptx(path: Path) -> Path:
    a = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
    pp = 'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'

    def slide(*paras: str, table: list[list[str]] = ()) -> str:
        body = "".join(f"<a:p><a:r><a:t>{t}</a:t></a:r></a:p>" for t in paras)
        rows = "".join("<a:tr>" + "".join(f"<a:tc><a:txBody><a:p><a:r><a:t>{c}</a:t></a:r></a:p></a:txBody></a:tc>"
                                          for c in row) + "</a:tr>" for row in table)
        grid = f"<p:graphicFrame><a:graphic><a:graphicData><a:tbl>{rows}</a:tbl></a:graphicData></a:graphic>" \
               "</p:graphicFrame>" if table else ""
        return XML + f"<p:sld {a} {pp}><p:cSld><p:spTree><p:sp><p:txBody>{body}</p:txBody></p:sp>{grid}" \
                     "</p:spTree></p:cSld></p:sld>"

    return make_zip(path, {
        # the deck's order isn't the files' order: slide3.xml is shown first
        "ppt/presentation.xml": XML + f'<p:presentation {pp} xmlns:r="{R}"><p:sldIdLst>'
                                      '<p:sldId id="256" r:id="rId3"/><p:sldId id="257" r:id="rId1"/>'
                                      '<p:sldId id="258" r:id="rId2"/></p:sldIdLst></p:presentation>',
        "ppt/_rels/presentation.xml.rels": XML + f'<Relationships {REL}>' + "".join(
            f'<Relationship Id="rId{n}" Type="{R}/slide" Target="slides/slide{n}.xml"/>' for n in (1, 2, 3))
            + "</Relationships>",
        "ppt/slides/slide3.xml": slide("Quarterly review", "Prepared by Sam"),
        "ppt/slides/slide1.xml": slide("Sales", table=[["Region", "Q3"], ["North", "1,200"], ["South", "950"]]),
        "ppt/slides/slide2.xml": slide("Next steps", "Hire two people")})


# ------------------------------------------------------------------ PDF

def test_a_short_pdf_is_read_whole_with_its_pages_marked(tmp_path):
    pdf = make_pdf(tmp_path / "letter.pdf", [["Dear Sam,", "Your account is open."], ["Kind regards,", "The bank"]])
    r = read(pdf)
    assert r.ok and r.text.splitlines() == ["letter.pdf (PDF, 2 pages):", "[page 1]", "Dear Sam,",
                                            "Your account is open.", "[page 2]", "Kind regards,", "The bank"]


def lines_of(page: int, n: int = 40) -> list[str]:
    return [f"Page {page} line {k}: rent, deposit, inventory and other things" for k in range(1, n + 1)]


def test_a_long_pdf_shows_its_start_and_end_and_never_reads_the_middle(tmp_path, monkeypatch):
    pdf = make_pdf(tmp_path / "manual.pdf", [lines_of(i) for i in range(1, 401)])
    doc = documents.load(pdf)
    asked = []
    real = doc.page
    monkeypatch.setattr(doc, "page", lambda i: asked.append(i) or real(i))
    start = time.perf_counter()
    r = read(pdf)
    assert time.perf_counter() - start < 3
    lines = r.text.splitlines()
    assert lines[0] == "manual.pdf (PDF, 400 pages):" and lines[1:3] == ["[page 1]", "Page 1 line 1: rent, deposit, "
                                                                                     "inventory and other things"]
    assert "Page 400 line 40: rent, deposit, inventory and other things" == lines[-2]
    assert "… the rest of page 1, pages 2–399 and the start of page 400 skipped …" in lines, r.text
    assert re.fullmatch(r"To read what was skipped: read_document again with pages='1-400', from_line=\d+, or with "
                        r"find='words'\.", lines[-1]), lines[-1]
    assert len(r.text) <= 1500 and len(set(asked)) <= 3


def test_pages_and_find_read_only_what_was_asked(tmp_path):
    pages = [lines_of(i, 10) for i in range(1, 6)]
    pages[3][4] = "Page 4: the boiler was serviced on 3 March"
    pdf = make_pdf(tmp_path / "report.pdf", pages)
    r = read(pdf, pages="3")
    assert r.text.splitlines()[:2] == ["report.pdf (PDF, 5 pages), page 3:", "[page 3]"]
    assert "Page 3 line 10" in r.text and "Page 2" not in r.text and "Page 4" not in r.text
    r = read(pdf, find="BOILER serviced")
    assert r.text.splitlines() == ["report.pdf (PDF, 5 pages): 1 line with 'BOILER serviced':", "[page 4]",
                                   "Page 4: the boiler was serviced on 3 March"]
    r = read(pdf, find="boiler", pages="1-3")
    assert r.ok and "no line has 'boiler'" in r.text
    assert not read(pdf, pages="9").ok and read(pdf, pages="9").text == "It has 5 pages, so there's no page 9."


def test_a_scanned_pdf_says_so_and_points_to_look(tmp_path):
    r = read(make_pdf(tmp_path / "scan.pdf", [None, None, None]))
    assert r.ok and "scanned" in r.text and "look" in r.text and "file_action" in r.text
    long_scan = read(make_pdf(tmp_path / "long scan.pdf", [None] * 60))   # too long to read every page
    assert "scanned" in long_scan.text


def test_a_pdf_with_some_scanned_pages_marks_them(tmp_path):
    r = read(make_pdf(tmp_path / "mixed.pdf", [["Typed cover letter"], None]))
    assert r.text.splitlines()[1:] == ["[page 1]", "Typed cover letter", "[page 2]",
                                       "(a picture with no text: look can read it)"]
    assert "look can read it" in read(tmp_path / "mixed.pdf", find="signature").text


def test_locked_and_damaged_pdfs(tmp_path):
    from pypdf import PdfReader, PdfWriter

    plain = make_pdf(tmp_path / "plain.pdf", [["Balance: 2,755.23"]])
    for name, user in (("owner only.pdf", ""), ("locked.pdf", "secret")):   # banks often lock with no password
        writer = PdfWriter()
        writer.append(PdfReader(plain))
        writer.encrypt(user_password=user, owner_password="owner", algorithm="AES-256")
        with open(tmp_path / name, "wb") as f:
            writer.write(f)
    assert "Balance: 2,755.23" in read(tmp_path / "owner only.pdf").text
    r = read(tmp_path / "locked.pdf")
    assert not r.ok and "locked with a password" in r.text
    (tmp_path / "broken.pdf").write_bytes(b"%PDF-1.4\n1 0 obj << /Type /Catalog >> nonsense")
    r = read(tmp_path / "broken.pdf")
    assert not r.ok and "damaged" in r.text


def test_a_real_statement_printed_by_a_browser():
    """Edge's PDF: subset fonts, a table across pages. The columns stay apart, so money in and money out differ."""
    r = read(FIXTURE)
    lines = r.text.splitlines()
    assert lines[0] == "statement.pdf (PDF, 3 pages):" and "Closing balance: £2,755.23" in lines
    header = next(line for line in lines if line.startswith("Date"))
    salary = next(line for line in lines if "SALARY" in line)
    netflix = next(line for line in lines if "Netflix" in line)
    assert header.split() == ["Date", "Description", "Paid", "out", "Paid", "in", "Balance"]
    assert salary.index("3,150.00") > header.index("Paid in") - 3 > netflix.index("72.88")  # in, not out
    assert len(r.text) <= 1500
    found = read(FIXTURE, find="netflix")
    assert found.text.startswith("statement.pdf (PDF, 3 pages): 8 lines with 'netflix':")
    assert found.text.count("Netflix") == 8 and "[page 1]" in found.text and "[page 2]" in found.text
    assert "finance" in read(FIXTURE, pages="last").text      # "ﬁnance": the ligature is read as two letters
    assert read(FIXTURE, find="3150").text.count("SALARY") == 2   # "3,150.00" found without its comma
    near = read(FIXTURE, find="salery").text                       # a word misspelt (or misheard)
    assert near.startswith("statement.pdf (PDF, 3 pages): 2 lines close to 'salery'") and near.count("SALARY") == 2


def test_squeezing_keeps_columns_apart():
    """Amounts right-aligned under their column's name stay right-aligned under it, in far fewer characters."""
    row = "   {:<14}{:<24}{:>12}{:>16}{:>14}".format
    layout = [row("Date", "Description", "Out", "In", "Balance"), row("01 Oct", "Netflix", "72.88", "", "2,377.12"),
              row("02 Oct", "SALARY ACME WIDGETS", "", "3,150.00", "5,527.12"), "",
              "        Thank you for banking with us"]
    out = squeeze(layout)
    head, netflix, salary = out[:3]
    end = lambda line, word: line.index(word) + len(word)
    assert end(netflix, "72.88") == end(head, "Out") and end(salary, "3,150.00") == end(head, "In")
    assert end(netflix, "2,377.12") == end(salary, "5,527.12") == end(head, "Balance")
    assert head.startswith("Date  ") and sum(map(len, out)) < sum(map(len, layout)) * 0.7
    assert out[3:] == ["", "Thank you for banking with us"]


# ------------------------------------------------------------------ Word, Excel, PowerPoint

def test_word_headings_lists_tables_and_pages(tmp_path):
    r = read(make_docx(tmp_path / "lease.docx", LEASE), room=4000)
    lines = r.text.splitlines()
    assert lines[0] == "lease.docx (Word document, 3 pages, as Word last laid it out):"
    assert lines[1:4] == ["[page 1]", "# Tenancy Agreement", "# Rent"]
    for line in ["## Payment", "The rent is £1,250 a month due on the 1st (updated)", "Page 1",
                 "See example.org for details.", "- Deposit: five weeks", "### Keep pets out of the garden",
                 "Date | Amount | Paid", "1 Oct | £1,250 | Yes / by card", " | £50 | No", "Signed: Sam Example",
                 "[page 2]", "# Schedule of condition", "[page 3]", "Inventory: one sofa."]:
        assert line in lines, f"{line!r} not in {lines}"
    assert "1,100" not in r.text and "PAGE" not in r.text   # deleted text and field codes aren't the document


def test_word_without_page_breaks_has_no_pages(tmp_path):
    r = read(make_docx(tmp_path / "note.docx", p("Buy milk") + p("Call the plumber")), pages="2")
    assert r.text.splitlines() == ["note.docx (Word document):", "Buy milk", "Call the plumber",
                                   "(A Word document has no pages: this is all of it.)"]


def test_excel_sheets_columns_and_dates(tmp_path):
    r = read(make_xlsx(tmp_path / "budget.xlsx"))
    assert r.text.splitlines() == [
        "budget.xlsx (Excel workbook, 2 sheets: Transactions, Rates):",
        "[sheet 1: Transactions]",
        "Date | Description | Paid out | Paid in | Cleared",
        "2026-10-02 | SALARY ACME WIDGETS |  | 3150 | TRUE",
        "2026-10-03 12:00 | Netflix | 72.88 |  | no",
        "[sheet 2: Rates]",
        "Savings rate | 0.15"]
    found = read(tmp_path / "budget.xlsx", find="netflix")
    assert found.text.splitlines()[1:] == ["[sheet 1: Transactions]", "Date | Description | Paid out | Paid in | Cleared",
                                           "2026-10-03 12:00 | Netflix | 72.88 |  | no"]
    assert read(tmp_path / "budget.xlsx", pages="2").text.endswith("Savings rate | 0.15")


def test_powerpoint_slides_in_the_decks_order(tmp_path):
    r = read(make_pptx(tmp_path / "review.pptx"))
    assert r.text.splitlines() == ["review.pptx (PowerPoint deck, 3 slides):", "[slide 1]", "Quarterly review",
                                   "Prepared by Sam", "[slide 2]", "Sales", "Region | Q3", "North | 1,200",
                                   "South | 950", "[slide 3]", "Next steps", "Hire two people"]
    assert read(tmp_path / "review.pptx", pages="last").text.endswith("Hire two people")


# ------------------------------------------------------------------ text

@pytest.mark.parametrize("name, data", [
    ("utf8.txt", "Café £5\nnaïve".encode("utf-8")),
    ("bom.txt", b"\xef\xbb\xbf" + "Café £5\nnaïve".encode("utf-8")),
    ("ansi.txt", "Café £5\nnaïve".encode("cp1252")),                 # Notepad's old default
    ("unicode.txt", b"\xff\xfe" + "Café £5\r\nnaïve".encode("utf-16-le")),  # Notepad's "Unicode"
    ("no mark.txt", "Café £5\r\nnaïve".encode("utf-16-le")),
    ("notes.md", "Café £5\n\n\n\nnaïve   ".encode("utf-8")),
])
def test_text_in_the_encodings_windows_writes(tmp_path, name, data):
    (tmp_path / name).write_bytes(data)
    lines = read(tmp_path / name).text.splitlines()
    assert lines[1:] in (["Café £5", "naïve"], ["Café £5", "", "naïve"]), lines


def test_csv_lines_found_come_under_their_column_names(tmp_path):
    rows = ["Date,Description,Amount,Balance"] + [f"{d:02d}/10/2026,Groceries,-{d}.50,{1000 - d}.00"
                                                  for d in range(1, 29)]
    rows[17] = "17/10/2026,NETFLIX.COM,-9.99,983.00"
    (tmp_path / "export.csv").write_text("\n".join(rows) * 10, encoding="utf-8")
    r = read(tmp_path / "export.csv", find="netflix")
    assert r.text.splitlines()[:3] == ["export.csv (CSV file): 10 lines with 'netflix':",
                                       "Date,Description,Amount,Balance", "17/10/2026,NETFLIX.COM,-9.99,983.00"]
    whole = read(tmp_path / "export.csv").text.splitlines()
    skipped = next(i for i, line in enumerate(whole) if line.startswith("… "))
    assert whole[skipped + 1] == "Date,Description,Amount,Balance"   # the end, under its column names


def test_html_without_its_tags(tmp_path):
    (tmp_path / "receipt.html").write_text(
        "<!doctype html><html><head><title>Receipt</title><style>p{color:red}</style><script>var x=1;</script>"
        "</head><body><h1>Order 1234</h1><table><tr><td>Kettle</td><td>£25.00</td></tr></table>"
        "<p>Thanks &amp; goodbye</p></body></html>", encoding="utf-8")
    assert read(tmp_path / "receipt.html").text.splitlines()[1:] == ["Receipt", "Order 1234", "Kettle | £25.00",
                                                                     "Thanks & goodbye"]


# ------------------------------------------------------------------ huge files

def test_a_huge_text_file_reads_only_its_start_and_end(tmp_path):
    big = tmp_path / "server.log"
    with open(big, "w", encoding="utf-8") as f:
        for n in range(1, 250_001):   # ~13 MB
            f.write(f"2026-10-01 12:00:{n % 60:02d} INFO request {n} served in {n % 97} ms\n")
        f.write("2026-10-31 23:59:59 ERROR disk full\n")
    start = time.perf_counter()
    r = read(big)
    assert time.perf_counter() - start < 2
    lines = r.text.splitlines()
    parts = -(-big.stat().st_size // 2**20)
    assert lines[0] == f"server.log (text file, {parts} parts, {big.stat().st_size / 2**20:.0f} MB, read in parts "                        "of 1 MB):"
    assert lines[1] == "[part 1]" and lines[2].endswith("request 1 served in 1 ms")
    assert lines[-2] == "2026-10-31 23:59:59 ERROR disk full" and len(r.text) <= 1500
    found = read(big, find="error")
    assert found.text.splitlines()[1:] == [f"[part {parts}]", "2026-10-31 23:59:59 ERROR disk full"]
    middle = read(big, find="request 123456 served")
    assert "request 123456 served in" in middle.text


def test_no_line_is_lost_or_doubled_between_parts(tmp_path, monkeypatch):
    monkeypatch.setattr(documents, "PART", 1000)
    monkeypatch.setattr(documents, "WHOLE_TEXT", 1000)
    for codec, mark in (("utf-8", b""), ("utf-16-le", b"\xff\xfe"), ("cp1252", b"")):
        text = "".join(f"{n}: {'é' * (n % 13)} £\n" for n in range(1, 700))
        path = tmp_path / f"{codec}.txt"
        path.write_bytes(mark + text.encode(codec))
        documents._docs.clear()
        doc = documents.load(path)
        got = [line for i in range(1, doc.count + 1) for line in doc.lines(i)]
        assert got == text.splitlines() and doc.count > 5, codec


def test_one_giant_line_still_fits(tmp_path):
    (tmp_path / "minified.json").write_text('{"a": "' + "x" * 3_000_000 + '"}', encoding="utf-8")
    r = read(tmp_path / "minified.json")
    assert r.ok and len(r.text) <= 1500


def test_finding_in_a_huge_document_stops_in_time_and_says_where(tmp_path, monkeypatch):
    monkeypatch.setattr(documents, "FIND_SECONDS", 0.0)
    pdf = make_pdf(tmp_path / "archive.pdf", [lines_of(i, 5) for i in range(1, 30)])
    r = read(pdf, find="nothing like this")
    assert "Only page 1 could be searched in the time; read_document again with pages='2-29' to search on." in r.text


def _every_line(doc) -> list[str]:
    return [line for i in range(1, doc.count + 1) for line in doc.lines(i) if line]


@pytest.mark.parametrize("room", [400, 1500])
def test_following_the_notes_reads_every_line(tmp_path, room):
    """However long its pages, a document is read to the end by doing what each answer says: one read, then the
    calls its note gives, each picking up where the last one stopped."""
    rows = "\n".join(f"{d:02d}/10/2026,Shop {d},-{d}.50,{1000 - d}.00" for d in range(1, 200))
    (tmp_path / "e.csv").write_text("Date,Description,Amount,Balance\n" + rows, encoding="utf-8")
    (tmp_path / "f.txt").write_text("\n".join(f"line {n} of a long note" for n in range(1, 300)), encoding="utf-8")
    files = [FIXTURE, make_pdf(tmp_path / "a.pdf", [lines_of(i, 30) for i in range(1, 5)]),
             make_docx(tmp_path / "b.docx", LEASE * 4), tmp_path / "e.csv", tmp_path / "f.txt"]
    for f in files:
        r, calls, seen = read(f, room=room), 1, []
        while True:
            seen += r.text.splitlines()
            ask = re.search(r"read_document again with (?:pages='([^']+)', )?from_line=(\d+)", r.text)
            if not ask:
                break
            r = read(f, pages=ask.group(1), from_line=ask.group(2), room=room)
            calls += 1
            assert r.ok and len(r.text) <= room and calls < 200, (f.name, r.text)
        missing = [line for line in _every_line(documents.load(f)) if line not in seen]
        assert not missing, (f.name, missing[:3])
        assert calls <= sum(len(x) + 1 for x in _every_line(documents.load(f))) / (room * 0.4) + 2, (f.name, calls)


def test_from_line_past_the_end(tmp_path):
    pdf = make_pdf(tmp_path / "short.pdf", [["one", "two"], ["three"]])
    assert read(pdf, pages="2", from_line=5).text == "Page 2 has 1 line, so there's no line 5."
    assert read(pdf, pages="1", from_line="two").text == "from_line should be a line number, not 'two'."
    assert read(pdf, from_line=2).text.splitlines() == ["short.pdf (PDF, 2 pages), from line 2:", "[page 1]", "two",
                                                        "[page 2]", "three"]


@pytest.mark.parametrize("room", [300, 700, 1500, 4000])
def test_every_answer_fits_the_room(tmp_path, room):
    files = [make_pdf(tmp_path / "a.pdf", [lines_of(i) for i in range(1, 30)]), make_docx(tmp_path / "b.docx", LEASE),
             make_xlsx(tmp_path / "c.xlsx"), make_pptx(tmp_path / "d.pptx"), FIXTURE]
    (tmp_path / "e.txt").write_text("\n".join("word " * 50 for _ in range(500)), encoding="utf-8")
    for f in files + [tmp_path / "e.txt"]:
        for kw in ({}, {"find": "a"}, {"pages": "1"}):
            r = read(f, room=room, **kw)
            assert r.ok and len(r.text) <= room, (f.name, kw, len(r.text))


# ------------------------------------------------------------------ not documents, and wrong paths

@pytest.mark.parametrize("name, data, says", [
    ("photo.jpg", b"\xff\xd8\xff\xe0" + bytes(200), "look"),
    ("photo with no name", b"\x89PNG\r\n\x1a\n" + bytes(200), "isn't text"),
    ("old.doc", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + bytes(600), "read_screen"),
    ("locked.xlsx", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + bytes(600), "locked with a password"),
    ("letter.rtf", b"{\\rtf1\\ansi Hello}", "read_screen"),
    ("empty.txt", b"", "empty"),
    ("data.bin", bytes(range(256)) * 4, "isn't text"),
])
def test_files_that_are_not_readable_documents_say_what_can_read_them(tmp_path, name, data, says):
    (tmp_path / name).write_bytes(data)
    r = read(tmp_path / name)
    assert not r.ok and says in r.text and name in r.text


def test_a_zip_is_not_a_word_document(tmp_path):
    make_zip(tmp_path / "photos.zip", {"a.txt": "x"})
    make_zip(tmp_path / "renamed.docx", {"a.txt": "x"})
    assert "archive" in read(tmp_path / "photos.zip").text and "archive" in read(tmp_path / "renamed.docx").text


@pytest.mark.parametrize("spec, pages", [
    ("2", [2]), (2, [2]), ("2-4", [2, 3, 4]), ("4-2", [2, 3, 4]), ("2–4", [2, 3, 4]), ("1, 3 and 5-6", [1, 3, 5, 6]),
    ("last", [10]), ("last 2", [9, 10]), ("first 3", [1, 2, 3]), ("3 to end", list(range(3, 11))),
    ("page 7", [7]), ("pages 2 and 9", [2, 9]), ("8-20", [8, 9, 10]), ([2, 3], [2, 3]),
])
def test_which_pages(spec, pages):
    assert which_pages(spec, 10) == pages


@pytest.mark.parametrize("spec", ["11", "zero", "page twelve"])
def test_pages_it_does_not_have(spec):
    with pytest.raises(documents.Unreadable):
        which_pages(spec, 10)


@pytest.fixture
def reg(tmp_path):
    make_pdf(tmp_path / "Bank Statement October 2026.pdf", [["Closing balance: 2,755.23"]])
    (tmp_path / "notes.txt").write_text("call the bank", encoding="utf-8")
    cfg = load_config()
    registry = ToolRegistry(ToolContext(cfg, AppIndex(cfg), Output()))
    return registry, tmp_path


def test_the_tool_reads_a_result_of_find_files(reg):
    registry, folder = reg
    registry.tools["find_files"].fn(folder=str(folder), file_type="pdf")
    for path in ["1", "#1", 1, "1. Bank Statement October 2026.pdf", "Bank Statement October 2026.pdf",
                 "Bank Statement October 2026.pdf | Desktop | Thu 29 Oct | 1 KB", str(folder / "Bank Statement "
                                                                                           "October 2026.pdf")]:
        r = registry.run("read_document", {"path": path})
        assert r.ok and "Closing balance: 2,755.23" in r.text, (path, r.text)


@pytest.mark.parametrize("path, says", [
    ("5", "There's no result 5 from find_files. Use find_files first."),
    ("C:/no/such/statement.pdf", "Can't find 'C:/no/such/statement.pdf'. Use find_files first."),
    ("notes.txt", "Can't find 'notes.txt'. Use find_files first."),    # not found by find_files: not a guess
])
def test_wrong_paths_say_what_to_do(reg, path, says):
    registry, _ = reg
    assert registry.run("read_document", {"path": path}).text == says


def test_a_folder_is_not_a_document(reg):
    registry, folder = reg
    r = registry.run("read_document", {"path": str(folder)})
    assert not r.ok and "is a folder" in r.text


def test_a_path_under_a_known_folder(reg, monkeypatch):
    registry, folder = reg
    (folder / "Bank").mkdir()
    (folder / "notes.txt").rename(folder / "Bank" / "notes.txt")
    monkeypatch.setattr("mary.winutil.known_folder", lambda name: folder)
    assert "call the bank" in registry.run("read_document", {"path": "Documents/…/Bank/notes.txt"}).text


def test_the_brick_is_switched_on_everywhere(reg):
    from mary import skills
    from mary.settings.choices import SKILLS

    registry, _ = reg
    tool = registry.tools["read_document"]
    assert tool.private and tool.watch is False and tool.required == ["path"]
    assert "documents" in skills.ALL and "documents" in load_config().skills and SKILLS["documents"][1] == "Everyday"
    guide = " ".join(skills.guides(skills.load(["documents"])))
    assert "read_document" in guide and "PowerShell" in guide and "screen" in guide
