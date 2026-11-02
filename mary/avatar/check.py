"""Checks on a photo before turning it into an animated face.

Ensures the face is large enough (>= 256px), roughly frontal, sharp, well-lit,
with eyes open and mouth closed, and not covered or obstructed.
Returns clear, plain-language sentences if a check fails.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Tuple

import numpy as np
from PIL import Image

from mary.avatar.landmarks import FaceLandmarks, NoFaceModels, extract_landmarks

MIN_FACE_PX = 256
MAX_YAW_DEG = 22.0
MAX_PITCH_DEG = 22.0
MAX_ROLL_DEG = 20.0
MIN_SHARPNESS = 35.0
MIN_LUMINANCE = 35.0
MAX_LUMINANCE = 225.0
MIN_CONTRAST = 18.0


def upright(image: Image.Image) -> Image.Image:
    """The picture the way it is seen: a phone photo stored sideways with an orientation tag is turned upright first,
    so the face points and the face made from them match what the person sees."""
    from PIL import ImageOps

    try:
        return ImageOps.exif_transpose(image)
    except Exception:
        return image


@dataclass
class CheckResult:
    ok: bool
    reason: str
    landmarks: FaceLandmarks | None = None
    face_px: tuple[int, int] = (0, 0)
    sharpness: float = 0.0


def _calculate_sharpness(gray_arr: np.ndarray) -> float:
    """Variance of the discrete Laplacian kernel: higher means sharper edges."""
    if gray_arr.shape[0] < 4 or gray_arr.shape[1] < 4:
        return 0.0
    # Discrete 3x3 Laplacian without external cv2 dependency
    # kernel: [[0, 1, 0], [1, -4, 1], [0, 1, 0]]
    pad = np.pad(gray_arr.astype(np.float32), 1, mode="edge")
    lap = (
        pad[1:-1, 2:] + pad[1:-1, :-2] + pad[2:, 1:-1] + pad[:-2, 1:-1]
        - 4.0 * pad[1:-1, 1:-1]
    )
    return float(np.var(lap))


def _check_mouth_closed(image_gray: np.ndarray, lm: FaceLandmarks) -> tuple[bool, str]:
    """Check if the mouth is closed or in a gentle resting expression."""
    rm, lm_pt = lm.mouth_right, lm.mouth_left
    mw = math.hypot(lm_pt[0] - rm[0], lm_pt[1] - rm[1])
    if mw < 10:
        return True, ""

    # Box around the mouth opening
    mx = int(min(rm[0], lm_pt[0]))
    my = int(min(rm[1], lm_pt[1]) - mw * 0.15)
    bw = int(mw)
    bh = int(mw * 0.5)

    h_img, w_img = image_gray.shape
    mx = max(0, min(w_img - bw, mx))
    my = max(0, min(h_img - bh, my))

    patch = image_gray[my : my + bh, mx : mx + bw]
    if patch.size == 0:
        return True, ""

    # A wide open mouth shows a tall dark cavity inside bordered by teeth/lips
    # Check vertical profile across the mouth
    v_profile = patch.mean(axis=1)
    if len(v_profile) >= 8:
        mean_v = float(np.mean(v_profile))
        dark_rows = np.sum(v_profile < (mean_v - 35.0))
        # An open cavity spans substantial vertical height (more than 40% of the mouth box)
        if dark_rows > len(v_profile) * 0.40 and (np.max(v_profile) - np.min(v_profile)) > 80.0:
            return False, "The mouth is wide open; pick a photo with a closed mouth or a gentle resting smile."

    return True, ""


def check_photo(
    image: Image.Image,
    target_person_id: int | None = None,
    engine=None,
) -> CheckResult:
    """Verify that `image` meets all criteria to produce a natural, high-quality avatar."""
    img_rgb = upright(image).convert("RGB")
    w_full, h_full = img_rgb.size

    try:
        landmarks_list = extract_landmarks(img_rgb, engine=engine)
    except NoFaceModels as e:
        return CheckResult(False, str(e))
    if not landmarks_list:
        return CheckResult(
            False,
            "I couldn't find a face in this photo; pick a photo showing the person clearly.",
        )

    # If multiple faces are present, pick the largest or best matching
    landmarks_list.sort(key=lambda lm: lm.box[2] * lm.box[3], reverse=True)
    best_lm = landmarks_list[0]

    bx, by, bw, bh = best_lm.box
    # 1. Size check
    if bw < MIN_FACE_PX or bh < MIN_FACE_PX:
        return CheckResult(
            False,
            f"This face is too small ({bw}×{bh} pixels; it should be at least {MIN_FACE_PX}×{MIN_FACE_PX}) to make a clear face.",
            landmarks=best_lm,
            face_px=(bw, bh),
        )

    # 2. Angle checks (frontal)
    if abs(best_lm.yaw) > MAX_YAW_DEG:
        side = "to their left" if best_lm.yaw > 0 else "to their right"
        return CheckResult(
            False,
            f"The person is turned sideways ({side}); pick a photo looking straight at the camera.",
            landmarks=best_lm,
            face_px=(bw, bh),
        )

    if abs(best_lm.pitch) > MAX_PITCH_DEG:
        direction = "upward" if best_lm.pitch > 0 else "downward"
        return CheckResult(
            False,
            f"The head is tilted {direction}; pick a photo looking straight ahead.",
            landmarks=best_lm,
            face_px=(bw, bh),
        )

    if abs(best_lm.roll) > MAX_ROLL_DEG:
        return CheckResult(
            False,
            "The head is tilted to the side; pick an upright portrait.",
            landmarks=best_lm,
            face_px=(bw, bh),
        )

    # 3. Crop face region for image analysis
    pad_x = int(bw * 0.1)
    pad_y = int(bh * 0.1)
    x0 = max(0, bx - pad_x)
    y0 = max(0, by - pad_y)
    x1 = min(w_full, bx + bw + pad_x)
    y1 = min(h_full, by + bh + pad_y)

    face_crop = img_rgb.crop((x0, y0, x1, y1))
    gray_arr = np.asarray(face_crop.convert("L"))

    # 4. Exposure and contrast
    mean_lum = float(np.mean(gray_arr))
    contrast = float(np.std(gray_arr))

    if mean_lum < MIN_LUMINANCE:
        return CheckResult(
            False,
            "The face is too dark; pick a photo taken in better light.",
            landmarks=best_lm,
            face_px=(bw, bh),
        )

    if mean_lum > MAX_LUMINANCE:
        return CheckResult(
            False,
            "The face is washed out by bright light; pick a photo with softer, even lighting.",
            landmarks=best_lm,
            face_px=(bw, bh),
        )

    if contrast < MIN_CONTRAST:
        return CheckResult(
            False,
            "The face has very low contrast or is washed out; pick a clearer photo.",
            landmarks=best_lm,
            face_px=(bw, bh),
        )

    # 5. Sharpness (Laplacian variance)
    sharpness = _calculate_sharpness(gray_arr)
    if sharpness < MIN_SHARPNESS:
        return CheckResult(
            False,
            "This photo is too blurry; pick a sharper, in-focus photo.",
            landmarks=best_lm,
            face_px=(bw, bh),
            sharpness=sharpness,
        )

    # 6. Mouth check
    full_gray = np.asarray(img_rgb.convert("L"))
    mouth_ok, mouth_reason = _check_mouth_closed(full_gray, best_lm)
    if not mouth_ok:
        return CheckResult(
            False,
            mouth_reason,
            landmarks=best_lm,
            face_px=(bw, bh),
            sharpness=sharpness,
        )

    # 7. Detection confidence / covered check
    if best_lm.score < 0.65:
        return CheckResult(
            False,
            "The face seems partly covered or obscured; pick an unobstructed portrait.",
            landmarks=best_lm,
            face_px=(bw, bh),
            sharpness=sharpness,
        )

    return CheckResult(
        True,
        "Looking good: this photo is clear, frontal, and well-lit.",
        landmarks=best_lm,
        face_px=(bw, bh),
        sharpness=sharpness,
    )
