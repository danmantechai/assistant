"""MARY's own browser: your installed Edge, driven by Playwright, with its own profile (data/browser/, private).

Web pages are read from their own structure (the text, where it sits, which parts are links, buttons or boxes and
what they're called), never from pixels, and acted on through Playwright, which waits for pages and elements by
itself and never moves your real mouse. Sign in once in this window (WhatsApp's QR code, YouTube) and it remembers.
Only visible text, roles and accessible names are used, never a site's own selectors, so a site that changes its
page doesn't break MARY.

Playwright's synchronous API belongs to the thread that started it, so one worker thread owns the browser and
everything else hands it jobs (Browser.call).
"""
from __future__ import annotations

import logging
import queue
import threading
from concurrent.futures import Future
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)

# Collected in the page: every visible piece of text with its box and what it belongs to, the controls with their
# accessible names, and where the keyboard is. Coordinates are the viewport's (the page area of the window).
_COLLECT = r"""
() => {
  const vw = innerWidth, vh = innerHeight, texts = [], controls = [], seen = new Set();
  const visible = r => r.width > 1 && r.height > 1 && r.bottom > 0 && r.right > 0 && r.top < vh && r.left < vw;
  const box = r => [Math.round(r.left), Math.round(r.top), Math.round(r.right), Math.round(r.bottom)];
  const hidden = el => { const s = getComputedStyle(el); return s.visibility === 'hidden' || s.display === 'none' || +s.opacity === 0; };
  const ROLES = {A: 'link', BUTTON: 'button', INPUT: 'edit', TEXTAREA: 'edit', SELECT: 'combo box'};
  const ACTIVE = new Set(['button', 'link', 'tab', 'menuitem', 'option', 'checkbox', 'radio', 'switch', 'combobox', 'textbox', 'searchbox']);
  const control = el => {
    for (let e = el; e && e !== document.body; e = e.parentElement) {
      const role = e.getAttribute('role');
      if (e.isContentEditable && (!e.parentElement || !e.parentElement.isContentEditable)) return [e, 'edit'];
      if (ROLES[e.tagName] && (e.tagName !== 'A' || e.hasAttribute('href'))) return [e, ROLES[e.tagName]];
      if (role && ACTIVE.has(role)) return [e, role === 'textbox' || role === 'searchbox' ? 'edit' : role];
    }
    return [null, 'text'];
  };
  const name = el => (el.getAttribute('aria-label') || el.innerText || el.getAttribute('title') ||
                      el.getAttribute('placeholder') || el.getAttribute('alt') || '').replace(/\s+/g, ' ').trim().slice(0, 200);
  const addControl = (el, role) => {
    if (!el || seen.has(el) || hidden(el)) return;
    seen.add(el);
    const r = el.getBoundingClientRect();
    if (visible(r)) controls.push({text: name(el), role, rect: box(r), id: controls.length});
    el.dataset.maryControl = String(controls.length - 1);
  };
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    let text = n.nodeValue.replace(/\s+/g, ' ').trim();
    const el = n.parentElement;
    if (!text || !el || hidden(el)) continue;
    const range = document.createRange(); range.selectNodeContents(n);
    const r = range.getBoundingClientRect();
    if (!visible(r)) continue;
    const label = el.getAttribute('aria-label');
    if (label && text.length <= 3) text = label;          // a badge "3" says "3 unread messages"
    const [owner, role] = control(el);
    addControl(owner, role);
    texts.push({text, role, rect: box(r), clickable: role !== 'text'});
  }
  for (const el of document.querySelectorAll('img[alt], [role=img][aria-label], svg[aria-label]')) {
    const r = el.getBoundingClientRect(), alt = (el.getAttribute('alt') || el.getAttribute('aria-label') || '').trim();
    if (alt && visible(r) && !hidden(el)) texts.push({text: alt, role: 'image', rect: box(r), clickable: false});
  }
  for (const el of document.querySelectorAll('button, a[href], input, textarea, select, [contenteditable=true], [role]')) {
    const [owner, role] = control(el);
    if (owner === el) {
      const before = controls.length; addControl(el, role);
      const c = controls[controls.length - 1];
      if (controls.length > before && c.text && !texts.some(t => t.rect[0] >= c.rect[0] && t.rect[2] <= c.rect[2] && t.rect[1] >= c.rect[1] && t.rect[3] <= c.rect[3]))
        texts.push({text: c.text, role, rect: c.rect, clickable: true});   // an icon button, an empty box: its name
    }
  }
  const a = document.activeElement, fr = a && a !== document.body ? a.getBoundingClientRect() : null;
  return {texts, controls, focus: fr ? [(fr.left + fr.right) / 2, (fr.top + fr.bottom) / 2] : null,
          w: vw, h: vh, title: document.title, url: location.href};
}
"""


class Browser:
    """One Edge window with MARY's profile. Every method is safe to call from any thread."""

    def __init__(self, profile: Path, headless: bool = False, channel: str = "msedge"):
        self.profile, self.headless, self.channel = profile, headless, channel
        self._jobs: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._context = self._page = self._pw = None

    # ---------------------------------------------------------------- the worker thread

    def call(self, job: Callable[[], Any], timeout: float = 60) -> Any:
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._work, name="mary-browser", daemon=True)
            self._thread.start()
        done: Future = Future()
        self._jobs.put((job, done))
        return done.result(timeout=timeout)

    def _work(self) -> None:
        while True:
            job, done = self._jobs.get()
            if job is None:
                done.set_result(None)
                return
            try:
                done.set_result(job())
            except BaseException as e:  # handed back to the caller
                done.set_exception(e)

    def _ensure(self):
        if self._context is None:
            from playwright.sync_api import sync_playwright

            self.profile.mkdir(parents=True, exist_ok=True)
            self._pw = sync_playwright().start()
            self._context = self._pw.chromium.launch_persistent_context(
                str(self.profile), channel=self.channel, headless=self.headless, no_viewport=True,
                args=["--start-maximized"])
            self._context.on("close", lambda *_: self._forget())
        pages = [p for p in self._context.pages if not p.is_closed()]
        if self._page is None or self._page.is_closed():
            self._page = pages[-1] if pages else self._context.new_page()
        return self._page

    def _forget(self) -> None:
        self._context = self._page = None

    def close(self) -> None:
        def stop():
            if self._context is not None:
                self._context.close()
            if self._pw is not None:
                self._pw.stop()
            self._context = self._page = self._pw = None
        if self._thread is not None and self._thread.is_alive():
            self.call(stop)
            done: Future = Future()
            self._jobs.put((None, done))

    # ---------------------------------------------------------------- what MARY does with it

    def open(self, url: str) -> dict:
        """Go to `url` (in the tab already showing that site, if there is one) and wait for it to load."""
        def go():
            page = self._ensure()
            host = url.split("/")[2] if "://" in url else url
            same = next((p for p in self._context.pages if not p.is_closed() and host in p.url), None)
            if same is not None and same.url.rstrip("/") == url.rstrip("/"):
                page = same
            else:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
            try:
                page.wait_for_load_state("load", timeout=10000)
            except Exception:
                pass  # still loading: the feedback loop and read() look again
            page.bring_to_front()
            self._page = page
            return {"title": page.title(), "url": page.url}
        return self.call(go)

    def snapshot(self) -> dict:
        """The page's text with its places, its controls with their names, the keyboard focus, and a screenshot."""
        def take():
            page = self._ensure()
            data = page.evaluate(_COLLECT)
            data["png"] = page.screenshot(type="png")
            return data
        return self.call(take)

    def click(self, control_id: int) -> str:
        def press():
            page = self._ensure()
            el = page.locator(f"[data-mary-control='{control_id}']").first
            el.click(timeout=8000)
            try:
                page.wait_for_load_state("domcontentloaded", timeout=8000)
            except Exception:
                pass
            return page.title()
        return self.call(press)

    def click_text(self, text: str) -> None:
        """Click where the page shows this text (a chat's name, a result's title): the click reaches whatever
        holds it, on any site."""
        def press():
            page = self._ensure()
            page.get_by_text(text, exact=False).first.click(timeout=8000)
        self.call(press)

    def type(self, control_id: int | None, text: str, submit: bool = False) -> None:
        def write():
            page = self._ensure()
            if control_id is not None:
                page.locator(f"[data-mary-control='{control_id}']").first.click(timeout=8000)
            page.keyboard.insert_text(text)
            if submit:
                page.keyboard.press("Enter")
        self.call(write)

    def scroll(self, direction: str) -> None:
        def move():
            page = self._ensure()
            keys = {"down": "PageDown", "up": "PageUp", "top": "Home", "bottom": "End"}
            page.keyboard.press(keys.get(direction, "PageDown"))
        self.call(move)

    def owns(self, pid: int) -> bool:
        """Is this process MARY's browser (an Edge started with her profile)?"""
        import psutil

        try:
            line = " ".join(psutil.Process(pid).cmdline()).lower()
        except (psutil.Error, OSError):
            return False
        return str(self.profile).lower() in line


_browser: Browser | None = None
_lock = threading.Lock()


ENABLED = True   # the tests switch it off: they never start the real one (they use their own, invisible)


def available() -> bool:
    import importlib.util

    return ENABLED and importlib.util.find_spec("playwright") is not None


def browser(cfg=None) -> Browser:
    """MARY's browser (started on first use)."""
    global _browser
    with _lock:
        if _browser is None:
            from mary.config import expand_path, load_config

            cfg = cfg or load_config()
            _browser = Browser(expand_path(cfg.paths.data) / "browser")
        return _browser


def owns(win) -> bool:
    """Is this window MARY's browser? (Without starting it.)"""
    return _browser is not None and win is not None and _browser.owns(win.pid)


def page_from(data: dict, title: str = ""):
    """A snapshot as mary.page's Page: the texts placed in the page's areas, found from its screenshot."""
    import io

    import numpy as np
    from PIL import Image

    from mary.page import Item, dividers, segment

    items = [Item(t["text"], t["role"], tuple(t["rect"]), t["clickable"]) for t in data["texts"]]
    vertical, horizontal = [], []
    if data.get("png"):
        pixels = np.asarray(Image.open(io.BytesIO(data["png"])).convert("L"))
        vertical, horizontal = dividers(pixels)
    page = segment(items, (int(data["w"]), int(data["h"])), vertical, horizontal, title=title or data["title"],
                   focus_point=tuple(data["focus"]) if data.get("focus") else None, source="page")
    return page
