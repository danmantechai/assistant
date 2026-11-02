"""Avatar animation director.

Controls live avatar frame generation:
  - Spontaneous natural eye blinks (half -> closed -> half -> open).
  - Attentive listening poses when the user speaks.
  - Thoughtful head shifts during model thinking.
  - Frame-accurate speech viseme lip synchronization from TTS audio.
"""
from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass
from typing import Optional

import numpy as np

from mary.avatar.visemes import analyze_visemes, cue_at_time

BLINK_HALF_MS = 0.040      # 40ms half-closed
BLINK_CLOSED_MS = 0.060    # 60ms fully-closed


@dataclass
class AvatarFrame:
    pose: str = "center"      # "center", "left", "right", "up", "down"
    eyes: str = "open"        # "open", "half", "closed"
    mouth: str = "X"          # "A" .. "H", "X"
    enabled: bool = True


class AvatarDirector:
    """Stateful animator delivering ~30Hz AvatarFrame updates."""

    def __init__(self, persona: str = "mary"):
        self.persona = persona
        self.lock = threading.Lock()
        self.state = "idle"

        # Blink timing
        self._next_blink = time.monotonic() + random.uniform(3.0, 5.5)
        self._blink_start: float | None = None

        # Micro head pose timing
        self._next_glance = time.monotonic() + random.uniform(5.0, 9.0)
        self._current_idle_pose = "center"
        self._idle_pose_until = 0.0

        # Speech viseme queue: one (start, end, cues) per sentence, on the speaker's clock. A reply's sentences are
        # made faster than they're spoken, so each is placed where it will actually play, after the ones before it.
        self._segments: list[tuple[float, float, list[tuple[float, str]]]] = []

    def set_state(self, state_name: str) -> None:
        """Update orchestrator state ('idle', 'listening', 'thinking', 'speaking', 'confirming')."""
        with self.lock:
            self.state = state_name
            if state_name == "listening":
                # Attentive pose
                self._current_idle_pose = "down"
                self._idle_pose_until = time.monotonic() + 9999.0
            elif state_name in ("thinking", "deep_thinking"):
                self._current_idle_pose = "up"
                self._idle_pose_until = time.monotonic() + 9999.0
            elif state_name == "speaking":
                self._current_idle_pose = "center"
            else:
                self._current_idle_pose = "center"
                self._idle_pose_until = 0.0

    def feed_audio(self, audio: np.ndarray, sample_rate: int = 24000, starts_at: float | None = None) -> None:
        """Queue a synthesized speech sentence for synchronized lip movements. `starts_at` (time.monotonic()) is when
        the speaker will start playing it; without it, it follows the sentences already queued."""
        cues = analyze_visemes(audio, sample_rate=sample_rate)
        duration = len(audio) / float(sample_rate)
        now = time.monotonic()
        with self.lock:
            self._segments = [seg for seg in self._segments if seg[1] > now]
            start = starts_at if starts_at is not None else max([now] + [seg[1] for seg in self._segments])
            self._segments.append((start, start + duration, cues))

    def stop_speech(self) -> None:
        """Stop any active speech visemes immediately (e.g. on user barge-in)."""
        with self.lock:
            self._segments = []

    def tick(self, now: float | None = None) -> AvatarFrame:
        """Calculate the active avatar pose, eye state, and mouth shape for the current frame."""
        t = now if now is not None else time.monotonic()
        with self.lock:
            # 1. Head Pose
            pose = self._current_idle_pose
            if self.state == "idle":
                if t >= self._next_glance:
                    # Pick a gentle micro-movement
                    choices = ["left", "right", "center"]
                    pose = random.choice(choices)
                    self._current_idle_pose = pose
                    self._idle_pose_until = t + random.uniform(1.2, 2.0)
                    self._next_glance = t + random.uniform(6.0, 10.0)
                elif t < self._idle_pose_until:
                    pose = self._current_idle_pose
                else:
                    pose = "center"
                    self._current_idle_pose = "center"

            # 2. Eye State (Blinking)
            eyes = "open"
            if self._blink_start is not None:
                elapsed_blink = t - self._blink_start
                if elapsed_blink < BLINK_HALF_MS:
                    eyes = "half"
                elif elapsed_blink < BLINK_HALF_MS + BLINK_CLOSED_MS:
                    eyes = "closed"
                elif elapsed_blink < BLINK_HALF_MS * 2 + BLINK_CLOSED_MS:
                    eyes = "half"
                else:
                    eyes = "open"
                    self._blink_start = None
                    self._next_blink = t + random.uniform(3.0, 5.5)
            elif t >= self._next_blink:
                self._blink_start = t
                eyes = "half"

            # 3. Mouth Viseme
            mouth = "X"
            for start, end, cues in self._segments:
                if start <= t < end and cues:
                    mouth = cue_at_time(cues, t - start)
                    break

            return AvatarFrame(pose=pose, eyes=eyes, mouth=mouth, enabled=True)
