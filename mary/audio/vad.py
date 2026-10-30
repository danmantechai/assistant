"""Silero VAD from its raw ONNX file (the silero-vad pip package would pull in PyTorch).

The v5+ model takes 512-sample chunks at 16 kHz plus the previous 64 samples as context, and carries
an LSTM state between calls, which is what silero-vad's own OnnxWrapper does.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

CHUNK, CONTEXT = 512, 64


class SileroVAD:
    def __init__(self, model_path: Path):
        import onnxruntime as ort

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 1  # tiny model; one thread is plenty and keeps idle CPU low
        opts.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(model_path), sess_options=opts,
                                            providers=["CPUExecutionProvider"])
        self.reset()

    def reset(self) -> None:
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, CONTEXT), dtype=np.float32)
        self._carry = np.zeros(0, dtype=np.float32)

    def _chunk_prob(self, chunk: np.ndarray) -> float:
        x = np.concatenate([self._context, chunk[None, :]], axis=1)
        out, self._state = self.session.run(None, {"input": x, "state": self._state,
                                                   "sr": np.array(16000, dtype=np.int64)})
        self._context = x[:, -CONTEXT:]
        return float(out[0][0])

    def probability(self, frame: np.ndarray) -> float:
        """Highest speech probability within a frame of int16 audio of any length."""
        audio = np.concatenate([self._carry, frame.astype(np.float32) / 32768.0])
        best, i = 0.0, 0
        while i + CHUNK <= len(audio):
            best = max(best, self._chunk_prob(audio[i:i + CHUNK]))
            i += CHUNK
        self._carry = audio[i:]
        return best
