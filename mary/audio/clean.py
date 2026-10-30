"""The microphone clean-up a browser does: WebRTC's audio processing (via LiveKit's build of it), the same noise
suppression, low-rumble filter and automatic volume Chrome applies before a web page hears the mic.

It runs on the request as it's recorded, in 10 ms pieces, and keeps adapting from one request to the next
(the volume control learns how loud you are). The wake word still hears the raw mic: it was trained on that.
Automatic volume can turn a loud mic down, but sound that already clipped in the mic stays distorted: only
the Windows input level (Settings › System › Sound › Input) fixes that.
"""
from __future__ import annotations

import numpy as np

from mary.audio.mic import RATE

PIECE = RATE // 100  # WebRTC works on 10 ms


class VoiceCleaner:
    def __init__(self, noise: bool = True, gain: bool = True):
        from livekit import rtc

        self._rtc = rtc
        self.apm = rtc.AudioProcessingModule(noise_suppression=noise, high_pass_filter=True, auto_gain_control=gain)
        self._carry = np.zeros(0, np.int16)

    def process(self, audio: np.ndarray) -> np.ndarray:
        """int16 16 kHz in, int16 out, the same length minus what doesn't fill a 10 ms piece yet (kept for
        the next call). The mic's 80 ms blocks are 8 whole pieces, so nothing waits there."""
        audio = np.concatenate([self._carry, audio.astype(np.int16)])
        whole = len(audio) // PIECE * PIECE
        self._carry = audio[whole:].copy()
        out = np.empty(whole, np.int16)
        for i in range(0, whole, PIECE):
            frame = self._rtc.AudioFrame(audio[i:i + PIECE].tobytes(), RATE, 1, PIECE)
            self.apm.process_stream(frame)
            out[i:i + PIECE] = np.frombuffer(frame.data, np.int16)
        return out


def make_cleaner(cfg) -> VoiceCleaner | None:
    clean = cfg.audio.get("clean") or {}
    if not clean.get("enabled", True):
        return None
    return VoiceCleaner(noise=clean.get("noise_suppression", True), gain=clean.get("auto_volume", True))
