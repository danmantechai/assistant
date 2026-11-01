"""web: open a URL / known site, or search the web: in MARY's own browser (mary/web, web.own_browser), where she can
read and act on pages through their own structure, or else in the default browser."""
from __future__ import annotations

import os
import re
import time
from urllib.parse import quote_plus

from mary.tools.registry import Tool, ToolContext, ToolResult

GUIDE = ('Websites and web searches: web. "Search YouTube for…" is a web search with site "youtube". A search only '
         "opens the results in the browser: to say what they are, call read_screen first. Never make up a link.")
GROUNDING = [
    ("web", lambda a: True,
     r"\b(search|google|look up|look for|open|go to|visit|find|check|show|website|site|page|web|online|internet|"
     r"youtube|wikipedia|amazon|browse|navigate|take me to|pull up|link|url|watch|read about|news|video)|\.\w{2,3}\b",
     "the user didn't ask to search or open a website"),
]
SLOW = {"web": ["One moment.", "Let me look that up."]}


def own_browser(cfg=None) -> bool:
    """Pages open in MARY's own browser: switched on, and Playwright installed."""
    from mary import web
    from mary.config import load_config

    cfg = cfg or load_config()
    return bool((cfg.get("web") or {}).get("own_browser", True)) and web.available()


def open_page(url: str, cfg=None) -> str | None:
    """Open `url` in MARY's own browser and wait for it to load: its title, or None when it opened elsewhere."""
    if own_browser(cfg):
        from mary import web

        try:
            return web.browser(cfg).open(url)["title"] or url
        except Exception as e:  # Edge missing, a crash: the default browser still works
            import logging

            logging.getLogger(__name__).warning("MARY's browser couldn't open %s: %s", url, e)
    os.startfile(url)  # only http(s) URLs reach here, so this always means "default browser"
    return None


def open_site(url: str) -> bool:
    """Open `url` (MARY's browser, else the default one). Returns True if an already-open tab was reused instead.

    In the default browser only WhatsApp Web is reused: it works in a single tab at a time, so a second copy
    breaks it. (MARY's browser reuses a tab already showing the page by itself.)
    """
    if own_browser():
        open_page(url)
        return False
    if "web.whatsapp.com" in url:
        from mary.skills.messages import focus_existing

        if focus_existing():
            return True
    os.startfile(url)  # only http(s) URLs reach here, so this always means "default browser"
    return False


def _bare(url: str) -> str:
    return re.sub(r"^https?://(www\.)?", "", url.strip().lower()).rstrip("/")


def tools(ctx: ToolContext) -> list[Tool]:
    sites = {k.lower(): v for k, v in (ctx.cfg.web.sites or {}).items()}
    search_url = ctx.cfg.web.search_url
    search_sites = {k.lower(): v for k, v in (ctx.cfg.web.get("search_sites") or {}).items()}

    def invented(url: str) -> bool:
        """A deep link (path or query) that appears nowhere: not in the user's words, not a known site, not in
        anything a tool showed. The model can't see the address behind a search result, and once opened a
        made-up YouTube link ("This video isn't available") for two different videos."""
        bare = _bare(url)
        if "/" not in bare and "?" not in bare or url in sites.values():
            return False
        known = " ".join([ctx.request, ctx.prev_request, *ctx.seen]).lower()
        return bare not in known and bare.split("?")[0] not in known

    def to_url(value: str) -> str | None:
        v = value.strip().strip("'\"")
        key = re.sub(r"\s+(website|site|dot com)$", "", v.lower()).strip()
        if key in sites:
            return sites[key]
        if re.match(r"(?i)^https?://", v):
            return v
        if re.match(r"(?i)^[\w-]+(\.[\w-]+)+(:\d+)?(/\S*)?$", v):  # bare domain like bbc.co.uk/news
            return "https://" + v
        return None

    def web(action: str, value: str, site: str = "") -> ToolResult:
        ctx.last_web_at = time.monotonic()  # "what did you find?" now means: read that page
        if action == "open_url":
            url = to_url(value)
            if url is None:
                return ToolResult(False, f"'{value}' isn't a URL or a known site. Use action 'search' instead.")
            if invented(url):
                return ToolResult(False, "Not opened: that address wasn't in what the user said or on screen, so "
                                         "it may be made up. To open a result, click it by its title with screen.")
            if own_browser(ctx.cfg):
                title = open_page(url, ctx.cfg)
                if title is not None:
                    return ToolResult(True, f"Opened {url} in MARY's browser: '{title}'. read_screen reads it.",
                                      say="Opening it now.", waits_for="front")
            elif open_site(url):
                return ToolResult(True, f"Switched to the open {url} tab.", say="It's already open; here it is.",
                                  waits_for="front")
            else:
                return ToolResult(True, f"Opened {url}.", say="Opening it now.", waits_for="front")
            return ToolResult(True, f"Opened {url} in the default browser.", say="Opening it now.", waits_for="front")
        if action == "search":
            template = search_sites.get(site.strip().lower(), search_url)  # "search YouTube for…": YouTube itself
            title = open_page(template.format(query=quote_plus(value)), ctx.cfg)
            # Say only what happened: the results are open, not read. "Here's what I found" once led the
            # model to invent the weather when asked "what did you find?".
            where = f"MARY's browser ('{title}')" if title else "the browser"
            return ToolResult(True, f"Opened a web search for '{value}' in {where}. The results haven't "
                                    "been read; read_screen can read them.",
                              say=f"I've opened a search for {value}"
                                  f"{' on ' + site.strip().title() if site.strip().lower() in search_sites else ''}"
                                  f" in your browser.", waits_for="front")
        return ToolResult(False, "action must be 'open_url' or 'search'.")

    return [Tool(
        "web",
        "Open a website (URL, domain, or a known site name like 'youtube') or run a web search, "
        "in MARY's browser.",
        {"action": {"type": "string", "enum": ["open_url", "search"]},
         "value": {"type": "string", "description": "URL/site name for open_url, or the search query"},
         "site": {"type": "string", "description": "search on this site instead of Google, e.g. youtube"}},
        ["action", "value"], web, speaks_for_itself=True,
        announce=lambda a: f"{'Searching for' if a.get('action') == 'search' else 'Opening'} {a.get('value', '')}…",
    )]
