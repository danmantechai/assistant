"""MARY's own browser (mary/web), for real: an invisible Edge with a throwaway profile, on local test pages (no
internet): a chat, search results, a form, a page that loads late. Skipped where Playwright or Edge isn't there."""
import tempfile
import time
from pathlib import Path

import pytest

from mary.web import Browser, page_from

PAGES = Path(__file__).parent / "fixtures" / "web"


@pytest.fixture(scope="module")
def browser():
    pytest.importorskip("playwright")
    b = Browser(Path(tempfile.mkdtemp(prefix="mary-browser-test-")), headless=True)
    try:
        b.open("about:blank")
    except Exception as e:
        pytest.skip(f"no Edge to drive: {e}")
    yield b
    b.close()


def url(name: str) -> str:
    return (PAGES / name).resolve().as_uri()


def control(b: Browser, starts: str) -> dict:
    return next(c for c in b.snapshot()["controls"] if c["text"].startswith(starts))


def test_a_chat_page_is_read_by_area_with_who_wrote_what(browser):
    assert browser.open(url("chat.html"))["title"] == "Chatter"
    page = page_from(browser.snapshot())
    assert [r.place for r in page.regions] == ["left panel", "main area"]
    main = page.main()
    assert page.focus is main and main.heading == "Ama" and main.chat_like
    assert ["← Pray ohh", "→ I sent her 100 on Thursday", "← Eei", "· Today", "→ Yh", "← Thanks boss"] == \
        [line for line in main.lines if line[:1] in "←→·"]
    assert "Meet me at the junction 2 unread messages" in page.region("left panel").lines   # a badge by its name


def test_clicking_by_name_opens_that_chat(browser):
    browser.open(url("chat.html"))
    browser.click(control(browser, "Kofi")["id"])
    main = page_from(browser.snapshot()).region("main area")
    assert main.heading == "Kofi" and "→ Coming" in main.lines


def test_a_result_is_clicked_by_its_title_and_the_page_changes(browser):
    browser.open(url("results.html"))
    page = page_from(browser.snapshot())
    assert "Sarkodie - Adonai (Official Video)" in page.main().lines
    browser.click(control(browser, "Sarkodie - Can't")["id"])
    assert page_from(browser.snapshot()).main().lines == ["Now playing video 2"]


def test_typing_into_a_box_and_pressing_enter(browser):
    browser.open(url("form.html"))
    browser.type(control(browser, "Search the site")["id"], "jollof", submit=True)
    browser.type(control(browser, "Your city")["id"], "Lisbon")
    browser.click(control(browser, "Save")["id"])
    lines = " ".join(page_from(browser.snapshot()).main().lines)
    assert "Saved Lisbon" in lines


def test_a_page_that_fills_in_late_is_read_once_it_has(browser):
    browser.open(url("slow.html"))
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:   # what the feedback loop does: look again while it changes
        lines = page_from(browser.snapshot()).main().lines
        if "Your statement is ready" in lines:
            break
        time.sleep(0.25)
    assert lines[:2] == ["Your statement is ready", "Download"]
