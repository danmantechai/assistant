"""web: open a URL / known site, or search the web, in the default browser."""
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


def open_site(url: str) -> bool:
    """Open `url` in the default browser. Returns True if an already-open tab was reused instead.

    Only WhatsApp Web is reused: it works in a single tab at a time, so a second copy breaks it.
    """
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
            if open_site(url):
                return ToolResult(True, f"Switched to the open {url} tab.", say="It's already open; here it is.")
            return ToolResult(True, f"Opened {url}.", say="Opening it now.")
        if action == "search":
            template = search_sites.get(site.strip().lower(), search_url)  # "search YouTube for…": YouTube itself
            os.startfile(template.format(query=quote_plus(value)))
            # Say only what happened: the results are open, not read. "Here's what I found" once led the
            # model to invent the weather when asked "what did you find?".
            return ToolResult(True, f"Opened a web search for '{value}' in the browser. The results haven't "
                                    "been read; read_screen can read them.",
                              say=f"I've opened a search for {value}"
                                  f"{' on ' + site.strip().title() if site.strip().lower() in search_sites else ''}"
                                  f" in your browser.")
        return ToolResult(False, "action must be 'open_url' or 'search'.")

    return [Tool(
        "web",
        "Open a website (URL, domain, or a known site name like 'youtube') or run a web search, "
        "in the default browser.",
        {"action": {"type": "string", "enum": ["open_url", "search"]},
         "value": {"type": "string", "description": "URL/site name for open_url, or the search query"},
         "site": {"type": "string", "description": "search on this site instead of Google, e.g. youtube"}},
        ["action", "value"], web, speaks_for_itself=True,
        announce=lambda a: f"{'Searching for' if a.get('action') == 'search' else 'Opening'} {a.get('value', '')}…",
    )]
