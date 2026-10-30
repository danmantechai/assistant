"""Microphone: one 16 kHz mono int16 stream in 80 ms blocks (openWakeWord's frame size)."""
from __future__ import annotations

import logging
import queue
import time

import numpy as np

log = logging.getLogger(__name__)

RATE = 16000
BLOCK = 1280  # 80 ms


def level_of(frame: np.ndarray) -> float:
    """Loudness 0..1 for the HUD waveform (log scale, so quiet speech still moves it)."""
    rms = float(np.sqrt(np.mean((frame.astype(np.float32) / 32768.0) ** 2)) + 1e-9)
    return float(np.clip((20 * np.log10(rms) + 55) / 45, 0.0, 1.0))  # -55 dBFS -> 0, -10 dBFS -> 1


class MicUnavailable(RuntimeError):
    """No microphone could be opened (none plugged in, or Windows' privacy settings block it)."""


def refresh_devices() -> None:
    """Make PortAudio re-scan the sound devices, so a mic plugged in after startup can be found.
    This closes every open stream, so only call it with the speaker idle (it reopens on its own)."""
    import sounddevice as sd

    try:
        sd._terminate()
        sd._initialize()
    except Exception as e:
        log.debug("device refresh failed: %s", e)


class Microphone:
    """Opened on demand: with no wake word, the mic is only on while MARY listens (and Windows' mic
    indicator says so); with a wake word it stays on, feeding frames to the detector."""

    def __init__(self, device=None):
        self.device = device
        self.frames: queue.Queue[np.ndarray] = queue.Queue(maxsize=200)  # ~16 s backlog at most
        self._stream = None
        self.last_frame_at = 0.0

    @property
    def active(self) -> bool:
        return self._stream is not None

    @property
    def broken(self) -> bool:
        """Open, but no audio for 2 s: unplugged, or another app took the device."""
        return self._stream is not None and time.monotonic() - self.last_frame_at > 2.0

    def start(self) -> None:
        if self._stream is not None:
            return
        import sounddevice as sd

        self.drain()
        try:
            stream = sd.InputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=BLOCK,
                                    device=self.device, callback=self._callback)
            stream.start()
        except Exception as e:  # sd.PortAudioError: no input device, or it's blocked
            raise MicUnavailable(str(e)) from e
        self.last_frame_at = time.monotonic()
        self._stream = stream

    def stop(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception as e:  # a device that vanished can fail to close cleanly
                log.debug("closing the mic stream failed: %s", e)

    def reopen(self) -> bool:
        """Try again after a failure: re-scan devices and open the (possibly new) mic."""
        self.stop()
        refresh_devices()
        try:
            self.start()
            return True
        except MicUnavailable as e:
            log.info("microphone still unavailable: %s", e)
            return False

    def _callback(self, indata, frames, time_info, status) -> None:
        self.last_frame_at = time.monotonic()
        if status:
            log.debug("mic status: %s", status)
        try:
            self.frames.put_nowait(indata[:, 0].copy())
        except queue.Full:  # nobody is reading (e.g. while MARY speaks): drop the oldest
            try:
                self.frames.get_nowait()
            except queue.Empty:
                pass

    def read(self, timeout: float = 1.0) -> np.ndarray | None:
        try:
            return self.frames.get(timeout=timeout)
        except queue.Empty:
            return None

    def drain(self) -> None:
        """Throw away buffered audio (e.g. MARY's own voice picked up while she was speaking)."""
        while True:
            try:
                self.frames.get_nowait()
            except queue.Empty:
                return
