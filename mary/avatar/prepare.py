"""Prepares an avatar face asset pack from a photo.

Generates:
  1. Base portrait crop (512x512) with feathered oval alpha mask.
  2. Eye state patches (open, half, closed) for natural blinking.
  3. Mouth shapes (Rhubarb / Preston Blair B-H) made from the person's own lips: the lower lip and jaw are moved and the
     gap between the lips shows the inside of the mouth. A and X are the photo's own closed mouth.
  4. Subtle micro-movement head poses (center, left, right, up, down), each with its own mouth and eye patches.
  5. Manifest describing the asset pack for HUD runtime rendering.
"""
from __future__ import annotations

import io
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from mary.avatar.check import CheckResult, check_photo, upright
from mary.avatar.landmarks import FaceLandmarks
from mary.avatar.store import FaceError, folder, validate

TARGET_SIZE = 512


def _feathered_oval_mask(size: tuple[int, int], margin: int = 24) -> Image.Image:
    """Generate a smooth alpha mask that gently fades to 0 at the edges."""
    w, h = size
    # Draw high-res oval then downsample with blur for smooth gradient
    oversample = 2
    sw, sh = w * oversample, h * oversample
    mask_img = Image.new("L", (sw, sh), 0)
    draw = ImageDraw.Draw(mask_img)

    pad_x = margin * oversample
    pad_y = int(margin * 1.2 * oversample)
    draw.ellipse((pad_x, pad_y, sw - pad_x, sh - pad_y), fill=255)

    # Smooth the edges
    blurred = mask_img.filter(ImageFilter.GaussianBlur(radius=margin * oversample * 0.6))
    return blurred.resize((w, h), Image.Resampling.BICUBIC)


def _crop_and_align_face(
    image: Image.Image,
    lm: FaceLandmarks,
) -> tuple[Image.Image, FaceLandmarks]:
    """Rotate image to make eyes horizontal and crop a 512x512 face frame."""
    # Rotate around eye midpoint if roll angle > 1.0 degree
    img = image.convert("RGBA")
    re, le = lm.right_eye, lm.left_eye
    mid_x = (re[0] + le[0]) / 2.0
    mid_y = (re[1] + le[1]) / 2.0

    if abs(lm.roll) > 1.0:
        # PIL turns counter-clockwise as seen; with y pointing down, rot_pt below turns the points clockwise by -roll,
        # the same turn: the picture and its points must move together.
        img = img.rotate(lm.roll, center=(mid_x, mid_y), resample=Image.Resampling.BICUBIC)
        # Recalculate landmarks after rotation
        rad = math.radians(-lm.roll)
        cos_a, sin_a = math.cos(rad), math.sin(rad)

        def rot_pt(pt):
            dx, dy = pt[0] - mid_x, pt[1] - mid_y
            return (mid_x + dx * cos_a - dy * sin_a, mid_y + dx * sin_a + dy * cos_a)

        re = rot_pt(re)
        le = rot_pt(le)
        nose = rot_pt(lm.nose)
        rm = rot_pt(lm.mouth_right)
        lmouth = rot_pt(lm.mouth_left)
        chin = rot_pt(lm.chin)
    else:
        nose = lm.nose
        rm = lm.mouth_right
        lmouth = lm.mouth_left
        chin = lm.chin

    # Calculate desired bounding region centered between eyes and mouth
    eye_dist = math.hypot(le[0] - re[0], le[1] - re[1])
    face_cx = mid_x
    face_cy = mid_y + eye_dist * 0.45

    # Crop box size: roughly 3.2x eye distance to capture hair, ears, chin
    crop_side = int(eye_dist * 3.4)
    crop_x0 = int(face_cx - crop_side / 2.0)
    crop_y0 = int(face_cy - crop_side * 0.45)
    crop_x1 = crop_x0 + crop_side
    crop_y1 = crop_y0 + crop_side

    # Crop with padding if extends beyond image bounds
    cropped = Image.new("RGBA", (crop_side, crop_side), (0, 0, 0, 0))
    src_x0 = max(0, crop_x0)
    src_y0 = max(0, crop_y0)
    src_x1 = min(img.width, crop_x1)
    src_y1 = min(img.height, crop_y1)

    paste_x = src_x0 - crop_x0
    paste_y = src_y0 - crop_y0
    patch = img.crop((src_x0, src_y0, src_x1, src_y1))
    cropped.paste(patch, (paste_x, paste_y))

    # Scale to 512x512
    scale = TARGET_SIZE / float(crop_side)
    base_512 = cropped.resize((TARGET_SIZE, TARGET_SIZE), Image.Resampling.LANCZOS)

    # Scale landmark points into 512x512 space
    def to_base(pt):
        return ((pt[0] - crop_x0) * scale, (pt[1] - crop_y0) * scale)

    re_b = to_base(re)
    le_b = to_base(le)
    nose_b = to_base(nose)
    rm_b = to_base(rm)
    lm_b = to_base(lmouth)
    chin_b = to_base(chin)
    mouth_mid_b = ((rm_b[0] + lm_b[0]) / 2.0, (rm_b[1] + lm_b[1]) / 2.0)

    aligned_lm = FaceLandmarks(
        box=(int((lm.box[0] - crop_x0) * scale), int((lm.box[1] - crop_y0) * scale),
             int(lm.box[2] * scale), int(lm.box[3] * scale)),
        left_eye=le_b,
        right_eye=re_b,
        nose=nose_b,
        mouth_left=lm_b,
        mouth_right=rm_b,
        mouth_center=mouth_mid_b,
        chin=chin_b,
        yaw=lm.yaw,
        pitch=lm.pitch,
        roll=0.0,
        eye_openness=lm.eye_openness,
        mouth_openness=lm.mouth_openness,
        score=lm.score,
    )

    # Apply feathered mask
    mask = _feathered_oval_mask((TARGET_SIZE, TARGET_SIZE), margin=28)
    base_512.putalpha(Image.composite(base_512.getchannel("A"), mask, mask))

    return base_512, aligned_lm


def _close_eyes(base_img: Image.Image, lm: FaceLandmarks, region, amount: float) -> Image.Image:
    """The face's own upper eyelids lowered by `amount` (0 open .. 1 shut): the lid skin above each eye is stretched
    down over it, never painted, with a soft lash line where it ends. Returns the region."""
    from scipy.ndimage import map_coordinates

    rx0, ry0, rw, rh = region
    ed = max(10.0, math.hypot(lm.left_eye[0] - lm.right_eye[0], lm.left_eye[1] - lm.right_eye[1]))
    ys, xs = np.mgrid[ry0:ry0 + rh, rx0:rx0 + rw].astype(np.float64)
    src_y = ys.copy()
    lash = np.zeros_like(ys)
    for ex, ey in (lm.left_eye, lm.right_eye):
        top, e0, e1 = ey - 0.25 * ed, ey - 0.10 * ed, ey + 0.11 * ed      # lid skin from here, the eye's top and bottom
        reach = _bump((xs - ex) / (0.32 * ed))                             # full at the eye's middle, none at its corners
        lid = e0 + amount * (e1 - e0) * reach                             # where the lid's edge comes to
        over = (ys >= top) & (ys < lid) & (reach > 0)
        stretched = top + (ys - top) * (e0 - top) / np.maximum(lid - top, 1e-3)
        src_y = np.where(over, stretched, src_y)
        lash = np.maximum(lash, amount * reach * np.exp(-((ys - lid) / 1.2) ** 2))

    arr = np.asarray(base_img.convert("RGBA"), dtype=np.float64)
    out = np.stack([map_coordinates(arr[:, :, c], [src_y, xs], order=1, mode="nearest") for c in range(4)], axis=-1)
    out[..., :3] *= (1 - 0.45 * lash)[..., None]
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGBA")


def _extract_eye_patches(
    base_img: Image.Image,
    lm: FaceLandmarks,
) -> tuple[tuple[int, int, int, int], Image.Image, Image.Image, Image.Image]:
    """(region, open, half-closed face, closed face): the blink states as whole faces, so each head pose cuts its own."""
    ed = max(10.0, math.hypot(lm.left_eye[0] - lm.right_eye[0], lm.left_eye[1] - lm.right_eye[1]))
    xs = (lm.left_eye[0], lm.right_eye[0])
    ys = (lm.left_eye[1], lm.right_eye[1])
    x0 = int(max(0, min(xs) - 0.45 * ed))
    x1 = int(min(TARGET_SIZE, max(xs) + 0.45 * ed))
    y0 = int(max(0, min(ys) - 0.32 * ed))
    y1 = int(min(TARGET_SIZE, max(ys) + 0.22 * ed))
    region = (x0, y0, x1 - x0, y1 - y0)

    def face(amount):
        f = base_img.copy()
        f.paste(_close_eyes(base_img, lm, region, amount), region[:2])
        return f

    return region, base_img.crop((x0, y0, x1, y1)), face(0.55), face(1.0)


# Mouth shapes (Rhubarb / Preston Blair): how far the lips part (as a share of the mouth's width), how narrow the
# mouth gets (rounded vowels), and whether the upper teeth show. A and X are the photo's own closed mouth.
VISEMES = {
    "B": (0.07, 1.00, True),     # lips just apart, teeth together: S, T, K
    "C": (0.17, 1.03, True),     # open: EH, AE
    "D": (0.26, 1.06, True),     # wide open: AA
    "E": (0.20, 0.86, False),    # rounded: AO, ER
    "F": (0.11, 0.72, False),    # puckered: UW, OW, W
    "G": (0.06, 0.97, True),     # lower lip under the teeth: F, V
    "H": (0.13, 1.00, True),     # tongue up: L
}


def _smooth(t):
    t = np.clip(t, 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _bump(u):
    """1 at 0, falling smoothly to 0 at |u| = 1."""
    return np.where(np.abs(u) < 1.0, 0.5 + 0.5 * np.cos(np.pi * np.clip(u, -1.0, 1.0)), 0.0)


def _mouth_region(lm: FaceLandmarks) -> tuple[int, int, int, int]:
    """The part of the face a mouth shape changes: the lips and the jaw below them, down to the chin."""
    (rx, ry), (lx, ly) = lm.mouth_right, lm.mouth_left
    mw = max(20.0, math.hypot(lx - rx, ly - ry))
    cx, cy = (rx + lx) / 2.0, (ry + ly) / 2.0
    eye_y = (lm.left_eye[1] + lm.right_eye[1]) / 2.0
    chin_y = cy + max(0.6 * mw, 0.8 * (cy - eye_y))
    x0 = int(max(0, cx - 1.15 * mw))
    x1 = int(min(TARGET_SIZE, cx + 1.15 * mw))
    y0 = int(max(0, cy - 0.6 * mw))
    y1 = int(min(TARGET_SIZE, chin_y + 0.25 * mw))
    return x0, y0, x1 - x0, y1 - y0


def _open_mouth(base_img: Image.Image, lm: FaceLandmarks, region, open_share: float, narrow: float,
                teeth: bool) -> Image.Image:
    """The face's own lips parted: the lower lip and jaw move down and the upper lip a little up (the picture is
    stretched, never painted over), and the gap between them is the inside of the mouth. Returns the region."""
    from scipy.ndimage import gaussian_filter, map_coordinates

    rx0, ry0, rw, rh = region
    (mrx, mry), (mlx, mly) = lm.mouth_right, lm.mouth_left
    mw = max(20.0, math.hypot(mlx - mrx, mly - mry))
    cx, lip_y = (mrx + mlx) / 2.0, (mry + mly) / 2.0
    bottom = ry0 + rh
    opening = open_share * mw

    ys, xs = np.mgrid[ry0:ry0 + rh, rx0:rx0 + rw].astype(np.float64)
    near_lips = np.exp(-((ys - lip_y) / (0.45 * mw)) ** 2)

    # Rounded vowels draw the mouth in toward its middle, most at the lips.
    k = 1.0 + (narrow - 1.0) * near_lips * _bump((xs - cx) / (1.1 * mw))
    src_x = cx + (xs - cx) / k

    lens = opening * _bump((xs - cx) / (0.55 * mw * narrow))      # the gap: widest in the middle, shut at the corners
    jaw = opening * _bump((xs - cx) / (1.1 * mw))                  # the chin moves down as a whole
    below = ys >= lip_y
    s = _smooth((ys - lip_y) / (0.35 * mw))
    fade = 1 - _smooth((ys - (lip_y + 0.55 * (bottom - lip_y))) / (0.45 * max(1.0, bottom - lip_y)))
    drop = (lens * (1 - s) + jaw * s) * fade
    lift = 0.22 * lens * (1 - _smooth((lip_y - ys) / (0.35 * mw)))
    src_y = np.where(below, ys - drop, ys + lift)

    arr = np.asarray(base_img.convert("RGBA"), dtype=np.float64)
    out = np.stack([map_coordinates(arr[:, :, c], [src_y, src_x], order=1, mode="nearest") for c in range(4)], axis=-1)

    # Inside the mouth: where the sample crossed the lip line, anti-aliased by how far it crossed.
    depth = np.where(below, lip_y - src_y, src_y - lip_y)
    inside = np.clip(depth, 0.0, 1.0) * (lens > 0.5)
    if inside.any():
        gap_top = lip_y - 0.22 * lens
        gap_h = np.maximum(1.0, 1.22 * lens)
        t = np.clip((ys - gap_top) / gap_h, 0.0, 1.0)              # 0 at the upper lip, 1 at the lower
        side = _bump((xs - cx) / (0.55 * mw * narrow))
        dark = np.array([74.0, 34.0, 32.0])
        colour = dark[None, None, :] * (0.55 + 0.9 * np.abs(t - 0.5))[..., None]   # darkest in the middle
        if teeth:
            band = (1 - _smooth((t - 0.16) / 0.16)) * (gap_h > 5) * np.clip(side * 1.4, 0, 1) * 0.85
            shade = 0.62 + 0.28 * side * _smooth(t / 0.12)      # in the upper lip's shadow, darker toward the corners
            teeth_c = np.array([208.0, 196.0, 182.0])[None, None, :] * shade[..., None]
            colour = colour * (1 - band[..., None]) + teeth_c * band[..., None]
        colour = np.stack([gaussian_filter(colour[..., c], 0.7) for c in range(3)], axis=-1)
        out[..., :3] = out[..., :3] * (1 - inside[..., None]) + colour * inside[..., None]
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGBA")


def _extract_mouth_patches(
    base_img: Image.Image,
    lm: FaceLandmarks,
) -> tuple[tuple[int, int, int, int], dict[str, Image.Image]]:
    """Every mouth shape as a whole face (the base with that region replaced), so each head pose can cut its own."""
    region = _mouth_region(lm)
    faces: dict[str, Image.Image] = {}
    for k, (open_share, narrow, teeth) in VISEMES.items():
        face = base_img.copy()
        face.paste(_open_mouth(base_img, lm, region, open_share, narrow, teeth), region[:2])
        faces[k] = face
    return region, faces


# Each head pose is a small shift and shear of the face (PIL affine: source = (a x + b y + c, d x + e y + f)).
POSES = {
    "center": (1.0, 0.0, 0.0, 0.0, 1.0, 0.0),
    "left": (1.0, 0.02, 4.0, 0.0, 1.0, 0.0),
    "right": (1.0, -0.02, -4.0, 0.0, 1.0, 0.0),
    "up": (1.0, 0.0, 0.0, 0.0, 1.0, 3.0),
    "down": (1.0, 0.0, 0.0, 0.0, 1.0, -3.0),
}


def _posed(img: Image.Image, name: str) -> Image.Image:
    if name == "center":
        return img
    return img.transform((TARGET_SIZE, TARGET_SIZE), Image.Transform.AFFINE, POSES[name],
                         resample=Image.Resampling.BICUBIC)


def _pose_rect(rect: tuple[int, int, int, int], name: str) -> tuple[int, int, int, int]:
    """Where `rect` of the straight-on face lands in a pose, grown to hold it whole."""
    a, b, c, d, e, f = POSES[name]
    x, y, w, h = rect
    corners = [(x, y), (x + w, y), (x, y + h), (x + w, y + h)]
    # The poses are shifts and shears (a = e = 1, d = 0): a source point (sx, sy) shows at (sx - b y - c, sy - f).
    xs = [px - b * (py - f) - c for px, py in corners]
    ys = [py - f for _, py in corners]
    x0, y0 = max(0, int(math.floor(min(xs))) - 1), max(0, int(math.floor(min(ys))) - 1)
    x1, y1 = min(TARGET_SIZE, int(math.ceil(max(xs))) + 1), min(TARGET_SIZE, int(math.ceil(max(ys))) + 1)
    return x0, y0, x1 - x0, y1 - y0


def _feather(size: tuple[int, int], ramp: int = 6) -> Image.Image:
    """Opaque inside, fading to clear over `ramp` pixels at the edges, so a patch has no visible border."""
    w, h = size
    xs = np.minimum(np.arange(w), np.arange(w)[::-1])
    ys = np.minimum(np.arange(h), np.arange(h)[::-1])
    ramp_x = np.clip((xs + 0.5) / ramp, 0, 1)
    ramp_y = np.clip((ys + 0.5) / ramp, 0, 1)
    return Image.fromarray((np.outer(ramp_y, ramp_x) * 255).astype(np.uint8), "L")


def _cut(face: Image.Image, rect: tuple[int, int, int, int]) -> Image.Image:
    x, y, w, h = rect
    patch = face.crop((x, y, x + w, y + h))
    alpha = np.asarray(patch.getchannel("A"), dtype=np.float64) * np.asarray(_feather((w, h)), dtype=np.float64) / 255
    patch.putalpha(Image.fromarray(alpha.astype(np.uint8), "L"))
    return patch


def prepare_face(
    image: Image.Image,
    persona: str,
    photo_id: int | None = None,
    person_id: int | None = None,
    engine=None,
) -> Path:
    """Validate photo, prepare all face assets, and install into persona folder."""
    # Step 1: Quality check, on the picture the way it is seen
    image = upright(image)
    check = check_photo(image, target_person_id=person_id, engine=engine)
    if not check.ok:
        raise FaceError(check.reason)

    assert check.landmarks is not None

    # Step 2: Align and crop base frame
    base_img, aligned_lm = _crop_and_align_face(image, check.landmarks)

    # Step 3: Eye patches
    eye_box, eye_open, eye_half, eye_closed = _extract_eye_patches(base_img, aligned_lm)

    # Step 4: Mouth viseme patches (A-H)
    mouth_box, mouth_shapes = _extract_mouth_patches(base_img, aligned_lm)

    # Step 5: Head poses, each with its own mouth and eye patches cut from the posed face, so they line up exactly
    files: dict[str, Image.Image] = {}
    poses_meta = []
    for p_name in POSES:
        files[f"pose_{p_name}.webp"] = _posed(base_img, p_name)
        m_rect, e_rect = _pose_rect(mouth_box, p_name), _pose_rect(eye_box, p_name)
        mouth = {}
        for k, face in mouth_shapes.items():
            name = f"mouth_{k.lower()}_{p_name}.webp"
            files[name] = _cut(_posed(face, p_name), m_rect)
            mouth[k] = {"file": name, "rect": list(m_rect)}
        eyes = {}
        for state, face in (("half", eye_half), ("closed", eye_closed)):
            name = f"eye_{state}_{p_name}.webp"
            files[name] = _cut(_posed(face, p_name), e_rect)
            eyes[state] = {"file": name, "rect": list(e_rect)}
        poses_meta.append({"name": p_name, "base": f"pose_{p_name}.webp", "mouth": mouth, "eyes": eyes})

    # Step 6: Manifest
    manifest_data = {
        "version": 1,
        "created": datetime.now(timezone.utc).isoformat(),
        "size": [TARGET_SIZE, TARGET_SIZE],
        "source": {"photo": photo_id, "person": person_id, "photo_id": photo_id, "person_id": person_id},
        "poses": poses_meta,
    }

    from mary.avatar import store

    def writer(tmp: Path) -> None:
        for name, img in files.items():
            img.save(tmp / name, "WEBP", quality=92)
        (tmp / store.MANIFEST).write_text(json.dumps(manifest_data, indent=2), encoding="utf-8")

    store.install(persona, writer)
    return store.folder(persona)
