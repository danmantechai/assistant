"""The settings page's web server: http://127.0.0.1:8765, this PC only.

Python's own http.server: the page (web/) and a small JSON API over mary.settings.store. No framework, nothing
fetched from the internet.

It guards against other websites open in your browser, which could otherwise try to reach a server on
127.0.0.1:
- it listens on 127.0.0.1 only
- the Host header must name this server, so a hostile name pointed at 127.0.0.1 ("DNS rebinding") is refused
- every API call needs the X-MARY header, which a page on another site can't send here without the server
  agreeing first (it never does)
- an Origin header, when present, must be this server's
- keys are only ever written, never sent back
"""
from __future__ import annotations

import base64
import binascii
import io
import json
import logging
import re
import threading
import wave
import zlib
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlsplit

import numpy as np

from mary import persona_io
from mary.config import expand_path, load_config
from mary.settings import choices, models, schema, store, wake
from mary.settings import memory as memory_page
from mary.settings import people as people_page
from mary.settings import photos as photos_page

log = logging.getLogger(__name__)
WEB_DIR = Path(__file__).parent / "web"
DEFAULT_PORT = 8765
MAX_BODY = 40_000_000           # a WhatsApp export as base64
_STATIC = {"/": ("index.html", "text/html; charset=utf-8"),
           "/index.html": ("index.html", "text/html; charset=utf-8"),
           "/settings.css": ("settings.css", "text/css; charset=utf-8"),
           "/settings.js": ("settings.js", "text/javascript; charset=utf-8"),
           "/favicon.svg": ("favicon.svg", "image/svg+xml"),
           "/recorder.js": ("recorder.js", "text/javascript; charset=utf-8")}
_CSP = ("default-src 'self'; img-src 'self' data:; media-src 'self' blob: https:; style-src 'self'; script-src 'self'; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")


@dataclass
class Hooks:
    """What a running MARY lends the page (nothing, when the page runs on its own)."""
    running: bool = False
    ready: bool = False                          # she's finished starting (the model is loaded)
    profile: object = None                       # her Profile: edits to it apply at once
    memory: object = None                        # her MemoryStore: the Memory page changes it at once
    photos: object = None                        # her PhotoLibrary and the worker that looks after the folders
    ingest: object = None
    kokoro: object = None                        # her Kokoro voice, for samples
    restart: Callable[[], None] | None = None    # restart MARY (None: not started by run.bat or in the background)
    pause_wake: Callable[[float], None] | None = None  # ignore the wake word for this many seconds (while recording)
    lock: threading.Lock = field(default_factory=threading.Lock)


class SettingsServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port: int = DEFAULT_PORT, hooks: Hooks | None = None):
        super().__init__(("127.0.0.1", port), Handler)
        self.hooks = hooks or Hooks()
        self.port = self.server_address[1]
        self.hosts = {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}
        self.origins = {f"http://{h}" for h in self.hosts}
        self.changed = False     # saved something that needs a restart
        self._tts = None
        self._memory = None
        self._memory_opened = False
        self._photos = None
        self._ingest = None
        self._photos_opened = False

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def memory(self):
        """The memory store: MARY's own while she runs, else one opened here (None when memory is switched off)."""
        if self.hooks.memory is not None:
            return self.hooks.memory
        with self.hooks.lock:
            if not self._memory_opened:
                from mary import memory

                self._memory = memory.open_store(load_config())
                if self._memory is not None:
                    self._memory.start()
                self._memory_opened = True
            return self._memory

    def photos(self):
        """(the photo library, its worker): MARY's own while she runs, else opened here, with the worker running until
        this window is closed (None, None when photos are switched off)."""
        if self.hooks.photos is not None:
            return self.hooks.photos, self.hooks.ingest
        memory = self.memory()
        with self.hooks.lock:
            if not self._photos_opened and memory is not None:
                from mary import photos
                from mary.photos.ingest import Ingest

                self._photos = photos.open_library(load_config(), memory)
                if self._photos is not None:
                    self._ingest = Ingest(self._photos)
                    self._ingest.start()
                self._photos_opened = True
            return self._photos, self._ingest

    def kokoro(self):
        with self.hooks.lock:
            if self.hooks.kokoro is None and self._tts is None:
                from mary.audio.tts import TextToSpeech

                cfg = load_config()
                self._tts = TextToSpeech(cfg, expand_path(cfg.paths.data) / "models")
            return self.hooks.kokoro or self._tts


# ------------------------------------------------------------------ the API


def _b64(body: dict) -> bytes:
    try:
        return base64.b64decode(body.get("data") or "", validate=True)
    except (binascii.Error, ValueError):
        raise store.SettingsError("That file didn't arrive whole; try again.") from None


def _wav(samples: np.ndarray, rate: int = 24000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())
    return buf.getvalue()


def state(srv: SettingsServer, *_):
    return {"settings": store.settings_state(), "personas": store.persona_list(),
            "profile": store.profile_info(srv.hooks.profile), "keys": store.key_status(),
            "skills": choices.skill_list(), "running": srv.hooks.running, "ready": srv.hooks.ready,
            "can_restart": srv.hooks.restart is not None, "changed": srv.changed}


def saved(srv: SettingsServer):
    srv.changed = True
    return state(srv)


def choice_list(srv, m, *_):
    kind = m["kind"]
    if kind == "ollama":
        return choices.ollama_models(load_config())
    if kind == "kokoro":
        return choices.kokoro_voices(load_config())
    if kind == "elevenlabs":
        return choices.elevenlabs_voices()
    return choices.microphones()


def api_models(srv, m, body, q):
    """The models a service offers, asked with the saved key (which never comes back to the page)."""
    try:
        url = schema.check("brain.api.url", body.get("url") or "")
    except ValueError as e:
        raise store.SettingsError(str(e)) from None
    return choices.api_models(url, store.read_key("LLM_API_KEY"))


def model_pull(srv, m, body, q):
    return models.start_pull(str(body.get("name") or ""))


def model_pulling(srv, m, body, q):
    return models.pull_status((q.get("name") or [""])[0])


def model_test(srv, m, body, q):
    return models.test(body)


def save_settings(srv, m, body, q):
    store.save_settings(body.get("changes"))
    return saved(srv)


def reset_setting(srv, m, body, q):
    store.reset_setting(body.get("path"))
    return saved(srv)


def persona_get(srv, m, *_):
    return store.persona_info(m["key"])


def persona_save(srv, m, body, q):
    store.save_persona(m["key"], body.get("fields"))
    saved(srv)
    return store.persona_info(m["key"])


def persona_use(srv, m, *_):
    store.use_persona(m["key"])
    return saved(srv)


def persona_delete(srv, m, *_):
    store.delete_persona(m["key"])
    return state(srv)


def persona_prompt(srv, m, *_):
    return store.prompt_preview(m["key"])


def persona_card(srv, m, body, q):
    card = persona_io.export_card(store.check_name(m["key"]), with_voice=q.get("voice") == ["1"])
    data = json.dumps(card, indent=2, ensure_ascii=False).encode("utf-8")
    return data, "application/json", f"{m['key']}.card.json"


def lore_save(srv, m, body, q):
    store.save_lore(m["key"], body.get("title"), body.get("keywords"), body.get("text"), body.get("old_file"))
    saved(srv)
    return store.persona_info(m["key"])


def lore_delete(srv, m, body, q):
    store.delete_lore(m["key"], body.get("file"))
    saved(srv)
    return store.persona_info(m["key"])


def texts_inspect(srv, m, body, q):
    return store.inspect_texts(str(body.get("filename") or "messages.txt"), _b64(body))


def texts_import(srv, m, body, q):
    count, name = store.import_texts(m["key"], str(body.get("filename") or "messages.txt"), _b64(body),
                                     body.get("sender") or None)
    saved(srv)
    return {"count": count, "file": name, "persona": store.persona_info(m["key"])}


def texts_delete(srv, m, body, q):
    store.delete_texts(m["key"], body.get("file"))
    saved(srv)
    return store.persona_info(m["key"])


def persona_new(srv, m, body, q):
    key = store.new_persona(body.get("key"), copy_from=body.get("copy_from"), answers=body.get("answers"))
    calls_you = str(body.get("calls_you") or "").strip()
    if calls_you:
        store.save_profile(calls_you, store.profile_info(srv.hooks.profile)["facts"], srv.hooks.profile)
    return {"key": key, "state": state(srv)}


def persona_import(srv, m, body, q):
    filename = str(body.get("filename") or "card")
    card = persona_io.parse_card(_b64(body), filename)
    d = card.get("data") or card
    key = store.check_name(str(body.get("as") or "").strip() or
                           re.sub(r"[^a-z0-9]+", "_", str(d.get("name") or "persona").lower()).strip("_")[:40] or
                           "persona")
    if store.mine_folder(key).exists():
        raise store.SettingsError(f"You already have a persona called {key}; give this one another name.")
    persona_io.persona_from_card(card, key)
    return {"key": key, "state": state(srv)}


def _arg(q, name, default=""):
    return (q.get(name) or [default])[0]


def memory_state(srv, m, body, q):
    return memory_page.state(srv.memory(), load_config())


def memory_list(srv, m, body, q):
    return memory_page.listing(srv.memory(), _arg(q, "kind", "fact"), _arg(q, "which", "current"), _arg(q, "day") or None,
                               int(_arg(q, "offset", "0") or 0))


def memory_search(srv, m, body, q):
    return memory_page.search(srv.memory(), _arg(q, "q"))


def memory_edit(srv, m, body, q):
    return memory_page.edit(srv.memory(), body.get("id"), body.get("text"))


def memory_end(srv, m, body, q):
    return memory_page.end(srv.memory(), body.get("id"))


def memory_delete(srv, m, body, q):
    return memory_page.remove(srv.memory(), body.get("id"))


def memory_add(srv, m, body, q):
    return memory_page.add(srv.memory(), body.get("text"))


def photos_state(srv, m, body, q):
    return photos_page.state(*srv.photos())


def people_state(srv, m, body, q):
    return people_page.state(*srv.photos())


def people_groups(srv, m, body, q):
    return people_page.groups(srv.photos()[0], q)


def people_faces(srv, m, body, q):
    return people_page.faces_of(srv.photos()[0], m["id"], q)


def people_face(srv, m, body, q):
    return people_page.crop(srv.photos()[0], m["id"])


def people_switch(srv, m, body, q):
    return people_page.switch(*srv.photos(), body.get("on"))


def people_models(srv, m, body, q):
    return people_page.get_models(*srv.photos())


def people_name(srv, m, body, q):
    return people_page.name(*srv.photos(), body)


def people_relation(srv, m, body, q):
    return people_page.relation(srv.photos()[0], body)


def people_unname(srv, m, body, q):
    return people_page.unname(srv.photos()[0], body)


def people_ignore(srv, m, body, q):
    return people_page.ignore(srv.photos()[0], body)


def people_unignore(srv, m, body, q):
    return people_page.unignore(*srv.photos(), body)


def people_merge(srv, m, body, q):
    return people_page.merge(*srv.photos(), body)


def people_split(srv, m, body, q):
    return people_page.split(srv.photos()[0], body)


def people_move(srv, m, body, q):
    return people_page.move(*srv.photos(), body)


def people_deny(srv, m, body, q):
    return people_page.deny(srv.photos()[0], body)


def people_not_same(srv, m, body, q):
    return people_page.not_same(srv.photos()[0], body)


def people_remove(srv, m, body, q):
    return people_page.remove(srv.photos()[0], body)


def people_delete_all(srv, m, body, q):
    return people_page.delete_all(srv.photos()[0])


def people_look_again(srv, m, body, q):
    return people_page.look_again(*srv.photos())


def photos_pick(srv, m, body, q):
    return {"path": photos_page.pick_folder()}


def photos_add(srv, m, body, q):
    lib, ing = srv.photos()
    return photos_page.add_folder(lib, ing, str(body.get("path") or ""), body.get("label"), body.get("recursive", True))


def photos_folder_edit(srv, m, body, q):
    return photos_page.folder_edit(srv.photos()[0], body.get("id"), body.get("label"), body.get("recursive"))


def photos_folder_remove(srv, m, body, q):
    return photos_page.folder_remove(srv.photos()[0], body.get("id"))


def photos_folder_album(srv, m, body, q):
    return photos_page.album(srv.photos()[0], body.get("id"), body)


def photos_places(srv, m, body, q):
    return photos_page.get_places(srv.photos()[0])


def photos_scan(srv, m, body, q):
    return photos_page.scan(*srv.photos())


def photos_list(srv, m, body, q):
    return photos_page.listing(srv.photos()[0], q)


def photos_facets(srv, m, body, q):
    return photos_page.facets(srv.photos()[0], q)


def photos_get(srv, m, body, q):
    return photos_page.photo(srv.photos()[0], m["id"])


def photos_similar(srv, m, body, q):
    return photos_page.similar(srv.photos()[0], m["id"])


def photos_thumb(srv, m, body, q):
    return photos_page.thumb(srv.photos()[0], m["id"])


def photos_view(srv, m, body, q):
    return photos_page.view(srv.photos()[0], m["id"])


def photos_show(srv, m, body, q):
    return photos_page.show(srv.photos()[0], m["id"])


def photos_fix(srv, m, body, q):
    return photos_page.fix(srv.photos()[0], body)


def photos_merge(srv, m, body, q):
    return photos_page.merge(srv.photos()[0], body)


def photos_remove(srv, m, body, q):
    return photos_page.remove(srv.photos()[0], body)


def profile_save(srv, m, body, q):
    store.save_profile(body.get("name"), body.get("facts"), srv.hooks.profile, body.get("about"))
    return state(srv)  # her own profile: no restart needed while she runs


def key_save(srv, m, body, q):
    store.set_key(body.get("name"), body.get("value"))
    srv.changed = True
    return store.key_status()


def credits(srv, *_):
    from mary.audio.cloud_tts import credits_left

    if not store.key_status()["elevenlabs"]:
        return {"known": False}
    got = credits_left()
    if not got:
        return {"known": False}
    used, limit, resets = got
    return {"known": True, "used": used, "limit": limit, "resets": resets}


def sample(srv, m, body, q):
    """A voice saying a line: Kokoro on this PC (cached), or ElevenLabs (costs credits; the page asks first)."""
    text = re.sub(r"\s+", " ", str(body.get("text") or "")).strip()[:300]
    if not text:
        raise store.SettingsError("Type something to say.")
    if body.get("provider") == "elevenlabs":
        from elevenlabs import ElevenLabs

        voice_id, model = str(body.get("voice_id") or ""), body.get("model") or "eleven_v4_turbo"
        if not re.fullmatch(r"[A-Za-z0-9]{8,64}", voice_id) or model not in store.VOICE_MODELS:
            raise store.SettingsError("Pick an ElevenLabs voice first.")
        if not store.key_status()["elevenlabs"]:
            raise store.SettingsError("Connect ElevenLabs first (Connections).")
        chunks = ElevenLabs().text_to_speech.convert(
            voice_id=voice_id, text=text, model_id=model, output_format="pcm_24000",
            request_options={"timeout_in_seconds": 20, "max_retries": 0})
        pcm = b"".join(chunks)
        audio = np.frombuffer(pcm[:len(pcm) // 2 * 2], np.int16).astype(np.float32) / 32768.0
    else:
        from mary.audio.tts import speakable

        voice = str(body.get("voice") or "af_heart")
        if not re.fullmatch(r"[a-z]{2}_[a-z]+", voice):
            raise store.SettingsError("Pick a Kokoro voice first.")
        speed = body.get("speed", 1.0)
        speed = min(2.0, max(0.5, float(speed))) if isinstance(speed, (int, float)) else 1.0
        cache = expand_path(load_config().paths.data) / "voice_cache" / "samples"
        file = cache / f"{voice}_{speed:.2f}_{zlib.crc32(text.encode()) % 10**8}.wav"
        if file.exists():
            return file.read_bytes(), "audio/wav", None
        tts = srv.kokoro()
        lang = "en-gb" if voice[:2] in ("bf", "bm") else "en-us"
        audio, _ = tts.kokoro.create(speakable(text), voice=voice, speed=speed, lang=lang)
        data = _wav(np.asarray(audio, dtype=np.float32))
        cache.mkdir(parents=True, exist_ok=True)
        file.write_bytes(data)
        return data, "audio/wav", None
    return _wav(np.asarray(audio, dtype=np.float32)), "audio/wav", None


def wake_plan(srv, m, body, q):
    return wake.plan((q.get("phrase") or [""])[0])


def wake_take(srv, m, body, q):
    if srv.hooks.pause_wake:
        srv.hooks.pause_wake(60)  # MARY doesn't answer her own wake word while you record
    return wake.save_take(str(body.get("phrase") or ""), str(body.get("kind") or ""), _b64(body), body.get("rate"))


def wake_pause(srv, m, body, q):
    seconds = body.get("seconds", 60)
    if srv.hooks.pause_wake and isinstance(seconds, (int, float)):
        srv.hooks.pause_wake(max(0.0, min(600.0, float(seconds))))
    return {"ok": True}


def wake_train(srv, m, body, q):
    status = wake.start_training(str(body.get("phrase") or ""))
    srv.changed = True
    return status


def wake_training(srv, m, body, q):
    return wake.training_status((q.get("phrase") or [""])[0]) or {"running": False}


def restart(srv, *_):
    if srv.hooks.restart is None:
        raise store.SettingsError("MARY wasn't started from here. Restart it yourself to use the changes.")
    threading.Timer(0.3, srv.hooks.restart).start()  # after this answer has gone out
    return {"ok": True}


_KEY = r"(?P<key>[a-z0-9][a-z0-9_-]{0,39})"
ROUTES = [(method, re.compile(pattern), fn) for method, pattern, fn in [
    ("GET", r"/api/ping", lambda *_: {"app": "mary-settings"}),
    ("GET", r"/api/state", state),
    ("GET", r"/api/choices/(?P<kind>ollama|kokoro|elevenlabs|mics)", choice_list),
    ("POST", r"/api/choices/api-models", api_models),
    ("POST", r"/api/models/pull", model_pull),
    ("GET", r"/api/models/pull", model_pulling),
    ("POST", r"/api/models/test", model_test),
    ("POST", r"/api/settings", save_settings),
    ("POST", r"/api/settings/reset", reset_setting),
    ("POST", r"/api/personas/new", persona_new),
    ("POST", r"/api/personas/import", persona_import),
    ("GET", rf"/api/personas/{_KEY}", persona_get),
    ("POST", rf"/api/personas/{_KEY}", persona_save),
    ("POST", rf"/api/personas/{_KEY}/use", persona_use),
    ("POST", rf"/api/personas/{_KEY}/delete", persona_delete),
    ("GET", rf"/api/personas/{_KEY}/prompt", persona_prompt),
    ("GET", rf"/api/personas/{_KEY}/card", persona_card),
    ("POST", rf"/api/personas/{_KEY}/lore", lore_save),
    ("POST", rf"/api/personas/{_KEY}/lore/delete", lore_delete),
    ("POST", rf"/api/personas/{_KEY}/texts/inspect", texts_inspect),
    ("POST", rf"/api/personas/{_KEY}/texts", texts_import),
    ("POST", rf"/api/personas/{_KEY}/texts/delete", texts_delete),
    ("POST", r"/api/profile", profile_save),
    ("GET", r"/api/memory", memory_state),
    ("GET", r"/api/memory/list", memory_list),
    ("GET", r"/api/memory/search", memory_search),
    ("POST", r"/api/memory/edit", memory_edit),
    ("POST", r"/api/memory/end", memory_end),
    ("POST", r"/api/memory/delete", memory_delete),
    ("POST", r"/api/memory/add", memory_add),
    ("GET", r"/api/photos", photos_state),
    ("POST", r"/api/photos/folders/pick", photos_pick),
    ("POST", r"/api/photos/folders/edit", photos_folder_edit),
    ("POST", r"/api/photos/folders/remove", photos_folder_remove),
    ("POST", r"/api/photos/folders/album", photos_folder_album),
    ("POST", r"/api/photos/folders", photos_add),
    ("POST", r"/api/photos/scan", photos_scan),
    ("POST", r"/api/photos/places", photos_places),
    ("GET", r"/api/photos/list", photos_list),
    ("GET", r"/api/photos/facets", photos_facets),
    ("POST", r"/api/photos/fix", photos_fix),
    ("POST", r"/api/photos/merge", photos_merge),
    ("POST", r"/api/photos/remove", photos_remove),
    ("GET", r"/api/photos/(?P<id>\d+)", photos_get),
    ("GET", r"/thumb/(?P<id>\d+)", photos_thumb),
    ("GET", r"/view/(?P<id>\d+)", photos_view),
    ("GET", r"/face/(?P<id>\d+)", people_face),
    ("GET", r"/api/people", people_state),
    ("GET", r"/api/people/groups", people_groups),
    ("GET", r"/api/people/(?P<id>\d+)/faces", people_faces),
    ("POST", r"/api/people/switch", people_switch),
    ("POST", r"/api/people/models", people_models),
    ("POST", r"/api/people/name", people_name),
    ("POST", r"/api/people/relation", people_relation),
    ("POST", r"/api/people/unname", people_unname),
    ("POST", r"/api/people/ignore", people_ignore),
    ("POST", r"/api/people/unignore", people_unignore),
    ("POST", r"/api/people/merge", people_merge),
    ("POST", r"/api/people/split", people_split),
    ("POST", r"/api/people/move", people_move),
    ("POST", r"/api/people/deny", people_deny),
    ("POST", r"/api/people/not-same", people_not_same),
    ("POST", r"/api/people/remove", people_remove),
    ("POST", r"/api/people/delete-all", people_delete_all),
    ("POST", r"/api/people/look-again", people_look_again),
    ("GET", r"/api/photos/(?P<id>\d+)/similar", photos_similar),
    ("POST", r"/api/photos/(?P<id>\d+)/show", photos_show),
    ("POST", r"/api/keys", key_save),
    ("GET", r"/api/credits", credits),
    ("POST", r"/api/sample", sample),
    ("GET", r"/api/wake/plan", wake_plan),
    ("POST", r"/api/wake/take", wake_take),
    ("POST", r"/api/wake/pause", wake_pause),
    ("POST", r"/api/wake/train", wake_train),
    ("GET", r"/api/wake/train", wake_training),
    ("POST", r"/api/restart", restart),
]]


# ------------------------------------------------------------------ HTTP


class Handler(BaseHTTPRequestHandler):
    server: SettingsServer
    server_version = "MARY-settings"
    sys_version = ""

    def log_message(self, fmt, *args):
        log.debug("settings page: " + fmt, *args)

    def do_GET(self):
        self._serve("GET")

    def do_POST(self):
        self._serve("POST")

    def _refused(self) -> str | None:
        if self.headers.get("Host") not in self.server.hosts:
            return "This page only answers at 127.0.0.1."
        if self.path.startswith(("/thumb/", "/view/", "/face/")) and self.headers.get("Sec-Fetch-Site") != "same-origin":
            return "Pictures are only for this page."   # an <img> sends no X-MARY header; the browser says who asked
        if self.path.startswith("/api/"):
            if self.headers.get("X-MARY") != "1":
                return "Missing the X-MARY header."
            origin = self.headers.get("Origin")
            if origin and origin not in self.server.origins:
                return "Another website can't change MARY's settings."
        return None

    def _serve(self, method: str) -> None:
        why = self._refused()
        if why:
            return self._json(403, {"error": why})
        url = urlsplit(self.path)
        if method == "GET" and url.path in _STATIC:
            name, kind = _STATIC[url.path]
            return self._send(200, (WEB_DIR / name).read_bytes(), kind)
        body = {}
        if method == "POST":
            if self.headers.get_content_type() != "application/json":
                return self._json(415, {"error": "Send JSON."})
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                return self._json(413, {"error": "That file is too big (30 MB at most)."})
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                return self._json(400, {"error": "Couldn't read the request."})
            if not isinstance(body, dict):
                return self._json(400, {"error": "Couldn't read the request."})
        for route_method, pattern, fn in ROUTES:
            m = pattern.fullmatch(url.path)
            if m and route_method == method:
                try:
                    out = fn(self.server, m, body, parse_qs(url.query))
                except (store.SettingsError, SystemExit) as e:
                    return self._json(400, {"error": str(e)})
                except Exception:
                    log.exception("settings page: %s %s failed", method, url.path)
                    return self._json(500, {"error": "Something went wrong; the details are in MARY's log."})
                if isinstance(out, tuple):
                    data, kind, filename = out
                    return self._send(200, data, kind, filename)
                return self._json(200, out)
        return self._json(404, {"error": "Not found."})

    def _json(self, status: int, payload) -> None:
        self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json")

    def _send(self, status: int, data: bytes, kind: str, filename: str | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        # a thumbnail's address carries its version, so it may be kept; everything else is always fresh, and a face is never
        # kept by the browser (a person who is deleted must not linger in its cache)
        keep = kind.startswith("image/") and not self.path.startswith("/face/")
        self.send_header("Cache-Control", "private, max-age=86400" if keep else "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if kind.startswith("image/"):
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        if kind.startswith("text/html"):
            self.send_header("Content-Security-Policy", _CSP)
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(data)


# ------------------------------------------------------------------ starting it

_server: SettingsServer | None = None


def start(port: int = DEFAULT_PORT, hooks: Hooks | None = None) -> SettingsServer:
    """Start the server on its own thread (once), on `port` or, if that's taken, any free one."""
    global _server
    if _server is not None:
        return _server
    try:
        srv = SettingsServer(port, hooks)
    except OSError:
        srv = SettingsServer(0, hooks)
    threading.Thread(target=srv.serve_forever, name="settings-page", daemon=True).start()
    log.info("settings page at %s", srv.url)
    _server = srv
    return srv
