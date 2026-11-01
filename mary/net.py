"""Direct requests: one shared HTTP client for every skill that needs data from the internet.

    data = net.get_json("https://wttr.in/Lisbon", params={"format": "j1"}, ttl=600)

- Timeouts on every request, and one retry for what can pass (no connection, a timeout, a 5xx or 429 answer).
- A small time-limited cache: asking twice within `ttl` seconds doesn't ask the site twice.
- Failures raise NetError, whose text is a plain sentence to say ("There's no internet connection right now."),
  with the status and the start of the answer for skills that read more into it.
Tests swap the transport (reset(httpx.MockTransport(...))), so nothing goes out.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from typing import Any
from urllib.parse import urlsplit

import httpx

log = logging.getLogger(__name__)

TIMEOUT = httpx.Timeout(8.0, connect=4.0)
RETRY_PAUSE_S = 0.4
CACHE_SIZE = 64
USER_AGENT = "MARY (local voice assistant)"
_DNS_FAILED = ("getaddrinfo", "name or service not known", "nodename nor servname", "no address associated",
               "temporary failure in name resolution", "11001")


class NetError(Exception):
    """A request that failed. str(e) is a plain sentence saying why, fit to tell the user."""

    def __init__(self, sentence: str, *, offline: bool = False, status: int | None = None, body: str = ""):
        super().__init__(sentence)
        self.offline = offline
        self.status = status
        self.body = body


_lock = threading.Lock()
_client: httpx.Client | None = None
_transport: httpx.BaseTransport | None = None
_cache: OrderedDict[tuple, tuple[float, Any]] = OrderedDict()
_now = time.monotonic


def reset(transport: httpx.BaseTransport | None = None) -> None:
    """A fresh client (on `transport`, for tests) and an empty cache."""
    global _client, _transport
    with _lock:
        if _client is not None:
            _client.close()
        _client, _transport = None, transport
        _cache.clear()


def client() -> httpx.Client:
    global _client
    with _lock:
        if _client is None:
            _client = httpx.Client(timeout=TIMEOUT, follow_redirects=True, transport=_transport,
                                   headers={"User-Agent": USER_AGENT})
        return _client


def get_json(url: str, *, params: dict | None = None, ttl: float = 300) -> Any:
    """The parsed JSON at `url`, from the cache if it was fetched less than `ttl` seconds ago."""
    return _get(url, params, ttl, as_json=True)


def get_text(url: str, *, params: dict | None = None, ttl: float = 300) -> str:
    return _get(url, params, ttl, as_json=False)


def _get(url: str, params: dict | None, ttl: float, as_json: bool) -> Any:
    key = (url, tuple(sorted((params or {}).items())), as_json)
    with _lock:
        hit = _cache.get(key)
        if hit and hit[0] > _now():
            _cache.move_to_end(key)
            return hit[1]
    resp = _fetch(url, params)
    if as_json:
        try:
            value = resp.json()
        except ValueError:
            raise NetError(f"{_host(url)} sent an answer I couldn't read.", status=resp.status_code,
                           body=resp.text[:300]) from None
    else:
        value = resp.text
    if ttl > 0:
        with _lock:
            _cache[key] = (_now() + ttl, value)
            _cache.move_to_end(key)
            while len(_cache) > CACHE_SIZE:
                _cache.popitem(last=False)
    return value


def _host(url: str) -> str:
    return urlsplit(url).hostname or url


def _fetch(url: str, params: dict | None) -> httpx.Response:
    """GET `url`, once more if the first try failed in a way that can pass."""
    host = _host(url)
    error: NetError | None = None
    for attempt in (1, 2):
        try:
            resp = client().get(url, params=params)
        except httpx.ConnectError as e:
            offline = any(s in str(e).lower() for s in _DNS_FAILED)
            error = NetError("There's no internet connection right now." if offline
                             else f"I can't reach {host} right now.", offline=offline)
        except httpx.TimeoutException:
            error = NetError(f"{host} didn't answer in time.")
        except httpx.HTTPError as e:
            error = NetError(f"The request to {host} failed ({type(e).__name__}).")
        else:
            if resp.status_code < 400:
                return resp
            error = NetError(f"{host} answered with an error ({resp.status_code}).", status=resp.status_code,
                             body=resp.text[:300])
            if resp.status_code < 500 and resp.status_code != 429:
                break  # asking again won't change "not found" or "not allowed"
        log.info("GET %s failed (try %d): %s", host, attempt, error)
        if attempt == 1 and RETRY_PAUSE_S:
            time.sleep(RETRY_PAUSE_S)
    assert error is not None
    raise error
