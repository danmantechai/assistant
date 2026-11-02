"""Audio to mouth viseme analysis for real-time lip synchronization.

Processes 24kHz float32 audio buffers (produced by Kokoro and ElevenLabs)
into timed Preston Blair / Rhubarb viseme cues:
  A: Closed / M, B, P
  B: Slightly open, teeth together / S, T, Z, CH
  C: Open, front vowels / EH, AE, AY
  D: Wide open / AA, AH
  E: Rounded / AO, ER, OW
  F: Puckered / UW, OW, W
  G: Lower lip under upper teeth / F, V
  H: Tongue behind teeth / L, TH
  X: Silence / rest
"""
from __future__ import annotations

import math
from typing import List, Tuple

import numpy as np

SAMPLE_RATE = 24000
FRAME_MS = 40          # 40ms frame window (~25 fps)
HOP_MS = 25            # 25ms step (~40 Hz update rate)


def analyze_visemes(
    audio: np.ndarray,
    sample_rate: int = SAMPLE_RATE,
    silence_rms: float = 0.003,          # below this there is no sound at all
) -> list[tuple[float, str]]:
    """Analyze float32 PCM audio into a sequence of (timestamp_seconds, viseme_shape).

    Every frame is judged against the sentence itself, never against fixed levels, so a quiet voice, a loud one and
    any TTS engine get the same shapes:
      openness   how loud the frame is next to the sentence's loud parts (the jaw opens with loudness)
      roundness  how dark its tone is next to the sentence's usual tone (rounded lips lower the spectrum)
      frication  hiss above 2.8 kHz with many zero crossings (S, T, F, V)
    A shape held for one frame only is dropped, so the mouth doesn't flicker."""
    if len(audio) == 0:
        return [(0.0, "X")]
    audio = np.asarray(audio, dtype=np.float32)

    frame_size = int(sample_rate * (FRAME_MS / 1000.0))
    hop_size = int(sample_rate * (HOP_MS / 1000.0))
    window = np.hanning(frame_size).astype(np.float32)
    freqs = np.fft.rfftfreq(frame_size, d=1.0 / sample_rate)
    voice_band = (freqs >= 100) & (freqs < 4000)
    high_band = (freqs >= 2800) & (freqs < 9000)

    frames = []
    for start in range(0, len(audio), hop_size):
        chunk = audio[start:start + frame_size]
        if len(chunk) < frame_size:
            chunk = np.pad(chunk, (0, frame_size - len(chunk)))
        rms = float(np.sqrt(np.mean(chunk ** 2)))
        signs = np.sign(chunk)
        zcr = float(np.mean(np.abs(signs[1:] - signs[:-1])) / 2.0)
        power = np.abs(np.fft.rfft(chunk * window)) ** 2
        voiced = float(np.sum(power[voice_band]))
        high = float(np.sum(power[high_band]))
        centroid = float(np.sum(freqs[voice_band] * power[voice_band]) / max(voiced, 1e-12))
        frames.append((round(start / float(sample_rate), 3), rms, zcr, high / max(voiced + high, 1e-12), centroid))

    loud = [f[1] for f in frames if f[1] >= silence_rms]
    ref = float(np.percentile(loud, 90)) if loud else 1.0
    quiet = max(silence_rms, 0.08 * ref)                  # quiet next to this sentence: the mouth rests
    tones = [f[4] for f in frames if f[1] >= quiet and f[3] < 0.35]
    usual_tone = float(np.median(tones)) if tones else 1000.0

    shapes: list[str] = []
    for _, rms, zcr, r_high, centroid in frames:
        level = rms / ref
        tone = centroid / max(usual_tone, 1.0)
        if rms < quiet:
            shape = "X"
        elif r_high > 0.35 or zcr > 0.3:
            shape = "B" if zcr > 0.35 else "G"          # S, T, Z / F, V
        elif tone < 0.8:
            shape = "F" if level < 0.5 else "E"         # OO, W / O, AW
        elif level < 0.3:
            shape = "B"
        elif tone > 1.3 and level < 0.65:
            shape = "H"                                 # L, TH: bright and not loud
        elif level < 0.65:
            shape = "C"
        else:
            shape = "D"
        shapes.append(shape)

    for i in range(1, len(shapes) - 1):               # a one-frame shape is noise
        if shapes[i] != shapes[i - 1] and shapes[i] != shapes[i + 1]:
            shapes[i] = shapes[i - 1]

    cues = [(f[0], s) for f, s in zip(frames, shapes)]
    cues.append((round(len(audio) / float(sample_rate), 3), "X"))
    return cues


def cue_at_time(cues: list[tuple[float, str]], elapsed_s: float) -> str:
    """Lookup the active viseme cue at `elapsed_s`."""
    if not cues:
        return "X"
    if elapsed_s <= cues[0][0]:
        return cues[0][1]
    if elapsed_s >= cues[-1][0]:
        return cues[-1][1]

    # Binary search
    lo = 0
    hi = len(cues) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        if cues[mid][0] <= elapsed_s:
            lo = mid + 1
        else:
            hi = mid - 1
    return cues[max(0, hi)][1]
