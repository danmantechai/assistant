"""Live hearing, as on the ElevenLabs dashboard: the request streams to Scribe's realtime model while you speak,
the words come back as you go (shown on the HUD), and the final text arrives a moment after you stop.

MARY still decides when you've finished (her pause rules, hold-to-talk); she then "commits" and Scribe returns
the transcript. The connection opens while she answers "Hey Mom", and audio is only sent once you start
speaking. The recording is kept as well, so if the live session fails the same audio goes to batch Scribe,
then to the local Whisper.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import threading
import time

import numpy as np

from mary.audio.stt import Heard, nothing_said

log = logging.getLogger(__name__)
_COMMIT, _CANCEL = object(), object()


class LiveLoop:
    """One asyncio loop on its own thread for every live session (MARY's voice loop is threaded)."""

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, name="live-stt", daemon=True).start()


class LiveSession:
    def __init__(self, loop: LiveLoop, connect, on_partial=None):
        """connect: a coroutine function returning an open realtime connection (the SDK's)."""
        self.loop, self.connect, self.on_partial = loop.loop, connect, on_partial
        self.started = time.perf_counter()
        self.committed_at = 0.0
        self.texts: list[str] = []
        self.error: str | None = None
        self.done = threading.Event()
        self.sent = 0
        self.queue: asyncio.Queue = asyncio.Queue()
        self.future = asyncio.run_coroutine_threadsafe(self._run(), self.loop)

    # ------------------------------------------------------------------ from the voice thread

    def feed(self, audio: np.ndarray) -> None:
        self.sent += len(audio)
        self.loop.call_soon_threadsafe(self.queue.put_nowait, audio.astype(np.int16).tobytes())

    def cancel(self) -> None:
        self.loop.call_soon_threadsafe(self.queue.put_nowait, _CANCEL)

    def finish(self, timeout: float) -> Heard | None:
        """Commit and wait for the final text: a Heard, or None if the session failed (use the recording)."""
        self.committed_at = time.perf_counter()
        self.loop.call_soon_threadsafe(self.queue.put_nowait, _COMMIT)
        if not self.done.wait(timeout) or self.error is not None:
            self.cancel()
            log.warning("live hearing failed: %s", self.error or f"no transcript within {timeout:.0f} s")
            return None
        text = " ".join(t.strip() for t in self.texts if t.strip())
        return Heard(text, text, time.perf_counter() - self.committed_at, 0.0 if text else -9.0, nothing_said(text))

    # ------------------------------------------------------------------ on the live loop

    async def _run(self) -> None:
        from elevenlabs import RealtimeEvents

        conn = None
        try:
            conn = await self.connect()
            committed = asyncio.Event()

            def on_committed(data):
                self.texts.append(data.get("text", ""))
                committed.set()

            def on_error(data):
                self.error = str(data)[:200]
                committed.set()

            conn.on(RealtimeEvents.PARTIAL_TRANSCRIPT, lambda d: self.on_partial and self.on_partial(d.get("text", "")))
            conn.on(RealtimeEvents.COMMITTED_TRANSCRIPT, on_committed)
            conn.on(RealtimeEvents.ERROR, on_error)
            while True:
                item = await self.queue.get()
                if item is _CANCEL:
                    return
                if item is _COMMIT:
                    if self.sent:
                        committed.clear()
                        await conn.commit()
                        await committed.wait()
                    self.done.set()
                    return
                await conn.send({"audio_base_64": base64.b64encode(item).decode()})
        except Exception as e:
            self.error = f"{type(e).__name__}: {e}"[:200]
            self.done.set()
        finally:
            if conn is not None:
                try:
                    await conn.close()
                except Exception:
                    pass
