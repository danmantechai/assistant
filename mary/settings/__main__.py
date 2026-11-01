"""python -m mary.settings (or settings.bat): the settings page on its own, for when MARY isn't running.

While she runs, open it from her tray menu instead (Settings…): then the page can restart her for you, and what
you change on the "You" page applies at once.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import urllib.request
import webbrowser

from mary.settings.server import DEFAULT_PORT, start


def already_open(port: int) -> bool:
    """Is a settings page (MARY's, or another window's) already answering on this port?"""
    request = urllib.request.Request(f"http://127.0.0.1:{port}/api/ping", headers={"X-MARY": "1"})
    try:
        with urllib.request.urlopen(request, timeout=1) as answer:  # noqa: S310 - our own address
            return json.load(answer).get("app") == "mary-settings"
    except (OSError, ValueError):
        return False


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m mary.settings", description=__doc__.split("\n\n")[0])
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-browser", action="store_true", help="don't open the page in your browser")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.WARNING, format="  %(message)s")

    if already_open(args.port):
        url = f"http://127.0.0.1:{args.port}/"
        print(f"MARY's settings page is already open: {url}")
        if not args.no_browser:
            webbrowser.open(url)
        return
    srv = start(args.port)
    print(f"MARY's settings: {srv.url}\n"
          "  Changes apply the next time MARY starts. Close this window (or press Ctrl+C) when you're done.")
    if not args.no_browser:
        webbrowser.open(srv.url)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
