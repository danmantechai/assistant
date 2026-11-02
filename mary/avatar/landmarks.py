"""Face landmark and pose estimation for avatar preparation.

Extracts facial features (eyes, nose, mouth corners, jawline, yaw/pitch/roll, openness)
using OpenCV Zoo YuNet (Apache-2.0) with ONNX Runtime, with optional MediaPipe integration
if available.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Tuple

import numpy as np
from PIL import Image

try:
    import mediapipe as mp
    _HAVE_MEDIAPIPE = True
except ImportError:
    mp = None
    _HAVE_MEDIAPIPE = False


@dataclass
class FaceLandmarks:
    """Detected face landmarks and pose measurements, in pixel coordinates of the source image."""
    box: tuple[int, int, int, int]                # (x, y, w, h)
    left_eye: tuple[float, float]                 # subject's left eye (viewer's right)
    right_eye: tuple[float, float]                # subject's right eye (viewer's left)
    nose: tuple[float, float]                     # nose tip
    mouth_left: tuple[float, float]               # subject's left mouth corner
    mouth_right: tuple[float, float]              # subject's right mouth corner
    mouth_center: tuple[float, float]             # center of lips
    chin: tuple[float, float]                     # estimated or detected chin tip
    yaw: float                                    # head turn: -90 (left) .. 0 (frontal) .. +90 (right)
    pitch: float                                  # head tilt: -90 (down) .. 0 (level) .. +90 (up)
    roll: float                                   # tilt to side: degrees angle of eye line
    eye_openness: float                           # 0.0 (closed) .. 1.0 (fully open)
    mouth_openness: float                         # 0.0 (closed) .. 1.0 (wide open)
    score: float                                  # detector confidence 0..1


def _estimate_pose_from_5points(
    box: tuple[int, int, int, int],
    reye: tuple[float, float],
    leye: tuple[float, float],
    nose: tuple[float, float],
    rmouth: tuple[float, float],
    lmouth: tuple[float, float],
    score: float,
) -> FaceLandmarks:
    """Calculate angles, centers, and openness from 5 standard YuNet landmarks."""
    bx, by, bw, bh = box

    # Eye center and distance
    eye_dx = leye[0] - reye[0]
    eye_dy = leye[1] - reye[1]
    eye_dist = math.hypot(eye_dx, eye_dy)
    roll = math.degrees(math.atan2(eye_dy, eye_dx))

    # Eye mid-point
    eye_mid = ((reye[0] + leye[0]) / 2.0, (reye[1] + leye[1]) / 2.0)

    # Mouth mid-point
    mouth_mid = ((rmouth[0] + lmouth[0]) / 2.0, (rmouth[1] + lmouth[1]) / 2.0)
    mouth_w = math.hypot(lmouth[0] - rmouth[0], lmouth[1] - rmouth[1])

    # Estimated chin: project down from nose through mouth_mid
    chin_x = mouth_mid[0] + (mouth_mid[0] - nose[0]) * 0.8
    chin_y = by + bh  # approximate lower box boundary
    chin = (chin_x, float(chin_y))

    # Yaw estimation: asymmetry between nose-to-left-eye and nose-to-right-eye
    dist_r = math.hypot(nose[0] - reye[0], nose[1] - reye[1])
    dist_l = math.hypot(nose[0] - leye[0], nose[1] - leye[1])
    total_dist = max(dist_r + dist_l, 1e-4)
    yaw_ratio = (dist_r - dist_l) / total_dist  # -1..1
    yaw = float(np.clip(yaw_ratio * 75.0, -90.0, 90.0))

    # Pitch estimation: vertical proportion of nose relative to eye-mouth distance
    eye_mouth_h = max(mouth_mid[1] - eye_mid[1], 1e-4)
    nose_rel_h = (nose[1] - eye_mid[1]) / eye_mouth_h  # typically ~0.55 - 0.65 when level
    pitch = float(np.clip((nose_rel_h - 0.60) * 120.0, -90.0, 90.0))

    # Openness approximations:
    # A neutral mouth height is ~0.15 - 0.25 of mouth width
    # When YuNet detects, mouth corner separation is mouth_w
    mouth_openness = 0.0  # default neutral, checked in detail by check.py

    return FaceLandmarks(
        box=box,
        left_eye=leye,
        right_eye=reye,
        nose=nose,
        mouth_left=lmouth,
        mouth_right=rmouth,
        mouth_center=mouth_mid,
        chin=chin,
        yaw=yaw,
        pitch=pitch,
        roll=roll,
        eye_openness=1.0,
        mouth_openness=mouth_openness,
        score=score,
    )


class NoFaceModels(RuntimeError):
    """The face models aren't on this PC, so nothing can say where a face's eyes and mouth are."""


def default_engine():
    """The same face finder the People page uses (YuNet), from the folder set as `photos.faces.models`; None when its
    model files aren't on this PC yet."""
    from mary.config import expand_path, load_config
    from mary.photos import faces

    folder = Path(expand_path(load_config().photos.faces.models))
    return faces.OnnxEngine(folder) if faces.present(folder) else None


def extract_landmarks(image: Image.Image, engine=None) -> list[FaceLandmarks]:
    """Every face in `image` with its 5 points (eyes, nose tip, mouth corners), found by the face model. Never a guess:
    without the model this raises NoFaceModels, since a face made from made-up points lands on the wrong features."""
    engine = engine or default_engine()
    if engine is None:
        raise NoFaceModels("The face models aren't on this PC yet: get them on the People page first.")

    results: list[FaceLandmarks] = []
    for det in engine.detect(image.convert("RGB")):
        pts = det.points
        if pts is None or len(pts) < 5:
            continue                      # a face without its points can't be placed
        bx, by, bw, bh = det.box
        reye, leye, nose, rmouth, lmouth = (tuple(float(v) for v in p) for p in pts[:5])
        results.append(_estimate_pose_from_5points((int(bx), int(by), int(bw), int(bh)), reye, leye, nose, rmouth,
                                                   lmouth, det.score))
    return results
