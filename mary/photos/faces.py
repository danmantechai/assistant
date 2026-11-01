"""Finding the faces in one picture and fingerprinting them. Everything stays on this PC.

An *engine* does the two jobs that need a model: `detect(image)` finds faces (a box and five landmarks: the eyes, the nose and
the corners of the mouth), and `embed(image, detection)` turns one into a fingerprint, a 128-number vector. The real engine
(OnnxEngine, below) runs two small models from the OpenCV Zoo with onnxruntime; the tests use a stand-in that reads invented
pictures from a table (mary.photos.synth), so the rules around the models are tested without any real face.

`analyse` is the part that is ours: which faces are clear enough to group or name at all (a small or unsure one is kept as a
box and never fingerprinted, so noise cannot make a group), a quality for each, and a small picture of each usable face to
show on the People page.
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Protocol

import numpy as np
from PIL import Image

CROP_PX = 128
MIN_PX = 48            # a face smaller than this (on its short side, in the picture analysed) is not fingerprinted
MIN_SCORE = 0.7        # nor one the detector is not sure is a face


@dataclass
class Detection:
    """A face as an engine sees it, in pixels of the picture it was given."""
    box: tuple[float, float, float, float]            # x, y, width, height
    points: tuple[tuple[float, float], ...]           # right eye, left eye, nose, right mouth corner, left mouth corner
    score: float                                      # 0..1


class Engine(Protocol):
    tag: str                                          # names the models: fingerprints of another tag are not comparable

    def detect(self, image: Image.Image) -> list[Detection]: ...

    def embed(self, image: Image.Image, detection: Detection) -> np.ndarray: ...


@dataclass
class Found:
    """A face as it is kept: where it is (fractions of the picture, so any size of it will do), how clear it is, and, if clear
    enough, its fingerprint and a small picture of it."""
    box: tuple[float, float, float, float]
    points: tuple[float, ...]
    score: float
    px: int
    quality: float
    vector: np.ndarray | None = None
    crop: bytes = field(default=b"", repr=False)

    @property
    def usable(self) -> bool:
        return self.vector is not None


def quality(score: float, px: int) -> float:
    """0..1: how much to trust this face as a description of the person (the detector's confidence, and its size)."""
    return float(min(1.0, max(0.0, score)) * min(1.0, max(0.25, px / 112)))


def _clamp(v: float) -> float:
    return float(min(1.0, max(0.0, v)))


def crop_jpeg(image: Image.Image, box: tuple[float, float, float, float]) -> bytes:
    """A small square picture of one face, with a little room round it."""
    w, h = image.size
    x, y, bw, bh = box
    side = min(max(bw, bh) * 1.6, float(min(w, h)))
    cx = min(max(x + bw / 2, side / 2), w - side / 2)
    cy = min(max(y + bh / 2, side / 2), h - side / 2)
    part = image.convert("RGB").crop((int(cx - side / 2), int(cy - side / 2), int(cx + side / 2), int(cy + side / 2)))
    part = part.resize((CROP_PX, CROP_PX), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    part.save(out, "JPEG", quality=85)
    return out.getvalue()


def analyse(image: Image.Image, engine: Engine, min_px: int = MIN_PX, min_score: float = MIN_SCORE) -> list[Found]:
    """The faces in a picture, best first. Only a clear one gets a fingerprint and a face picture."""
    w, h = image.size
    out = []
    for det in engine.detect(image):
        x, y, bw, bh = det.box
        px = int(min(bw, bh))
        clear = det.score >= min_score and px >= min_px and bw > 0 and bh > 0
        vector = None
        if clear:
            v = np.asarray(engine.embed(image, det), dtype=np.float32)
            n = float(np.linalg.norm(v))
            vector = v / n if n > 0 and np.isfinite(n) else None
        pts = tuple(_clamp(c / (w if i % 2 == 0 else h)) for p in det.points for i, c in enumerate(p))
        out.append(Found(
            box=(_clamp(x / w), _clamp(y / h), _clamp(bw / w), _clamp(bh / h)), points=pts, score=float(det.score), px=px,
            quality=quality(det.score, px), vector=vector, crop=crop_jpeg(image, det.box) if vector is not None else b""))
    out.sort(key=lambda f: -f.quality)
    return out


# ---------------------------------------------------------------------- the real models

DETECTOR = "face_detection_yunet_2023mar.onnx"      # YuNet, MIT licence, 233 KB
RECOGNISER = "face_recognition_sface_2021dec.onnx"  # SFace, Apache-2.0, 38.7 MB
# Both from the OpenCV Zoo (github.com/opencv/opencv_zoo, mirrored on huggingface.co/opencv). OpenCV does not say what SFace was
# trained on beyond "public research face sets": for a hosted product that needs its own licence review.
MODELS = {      # the sizes and hashes are those of the files as measured, and as Hugging Face publishes them
    DETECTOR: {"url": "https://huggingface.co/opencv/face_detection_yunet/resolve/main/" + DETECTOR, "bytes": 232_589,
               "sha256": "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"},
    RECOGNISER: {"url": "https://huggingface.co/opencv/face_recognition_sface/resolve/main/" + RECOGNISER, "bytes": 38_696_353,
                 "sha256": "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79"},
}
# Where the five points of an aligned 112 x 112 face go (the standard layout both models were made with).
TEMPLATE = np.array([[38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366], [41.5493, 92.3655], [70.7299, 92.2041]],
                    dtype=np.float64)
ALIGNED = 112
CANDIDATE = 0.6       # YuNet candidates below this are never looked at (below it they are chins, shirts and posters)
NMS_IOU = 0.3
INSIDE = 0.7          # a box this much inside a better one is the same face seen in a piece
TILE_OVERLAP = 128    # tiles of a big picture overlap by this much: a face up to this size is whole in at least one


def similarity(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """The 2 x 3 matrix of the rotation, scale and shift that carries the points `src` closest onto `dst` (Umeyama)."""
    src, dst = np.asarray(src, dtype=np.float64), np.asarray(dst, dtype=np.float64)
    mu_s, mu_d = src.mean(axis=0), dst.mean(axis=0)
    s0, d0 = src - mu_s, dst - mu_d
    var = float((s0 ** 2).sum() / len(src))
    cov = d0.T @ s0 / len(src)
    u, sing, vt = np.linalg.svd(cov)
    flip = np.ones(2)
    if np.linalg.det(cov) < 0:
        flip[-1] = -1.0
    rot = u @ np.diag(flip) @ vt
    scale = float((sing * flip).sum() / var) if var > 0 else 1.0
    return np.hstack([scale * rot, (mu_d - scale * rot @ mu_s)[:, None]])


def align(image: Image.Image, points) -> Image.Image:
    """The face as 112 x 112 pixels, upright and scaled so its eyes, nose and mouth sit where the models expect them."""
    m = similarity(np.asarray(points, dtype=np.float64).reshape(5, 2), TEMPLATE)
    inv = np.linalg.inv(m[:, :2])
    shift = -inv @ m[:, 2]
    return image.convert("RGB").transform((ALIGNED, ALIGNED), Image.Transform.AFFINE,
                                          (inv[0, 0], inv[0, 1], shift[0], inv[1, 0], inv[1, 1], shift[1]),
                                          resample=Image.Resampling.BILINEAR)


def nms(boxes: np.ndarray, scores: np.ndarray, threshold: float = NMS_IOU) -> list[int]:
    """The boxes to keep when several cover one face: the best first, then any that do not overlap it much and are not mostly
    inside it (looking at a big face in pieces, the model sometimes finds a small "face" in the chin or the neck)."""
    order = np.argsort(-scores)
    keep: list[int] = []
    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 0] + boxes[:, 2], boxes[:, 1] + boxes[:, 3]
    area = boxes[:, 2] * boxes[:, 3]
    while order.size:
        i = int(order[0])
        keep.append(i)
        rest = order[1:]
        w = np.maximum(0.0, np.minimum(x2[i], x2[rest]) - np.maximum(x1[i], x1[rest]))
        h = np.maximum(0.0, np.minimum(y2[i], y2[rest]) - np.maximum(y1[i], y1[rest]))
        inter = w * h
        iou = inter / np.maximum(area[i] + area[rest] - inter, 1e-9)
        inside = inter / np.maximum(area[rest], 1e-9)
        order = rest[(iou <= threshold) & (inside < INSIDE)]
    return keep


def models_folder(lib) -> "Path":
    from pathlib import Path

    from mary.config import expand_path

    return Path(expand_path(lib.setting("faces.models")))


def present(folder) -> bool:
    from pathlib import Path

    return all((Path(folder) / name).exists() and (Path(folder) / name).stat().st_size > 1000 for name in MODELS)


def load(lib) -> "OnnxEngine | None":
    """The real engine, or None when the two model files are not on this PC yet (they are fetched on the People page)."""
    folder = models_folder(lib)
    return OnnxEngine(folder, int(lib.setting("faces.threads"))) if present(folder) else None


def fetch(folder, get=None) -> dict:
    """Fetch the two model files (about 39 MB in all) into `folder`. Only ever run because the person pressed the button that
    says so; it sends nothing of theirs. Each file is checked (size, and its hash once pinned) before it is kept."""
    import hashlib
    from pathlib import Path

    def default_get(url: str) -> bytes:
        import httpx

        r = httpx.get(url, timeout=300, follow_redirects=True)
        r.raise_for_status()
        return r.content

    get = get or default_get
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    got = {}
    for name, info in MODELS.items():
        data = get(info["url"])
        if len(data) < 1000 or (info["sha256"] and hashlib.sha256(data).hexdigest() != info["sha256"]):
            raise ValueError(f"{name} is not the file that was expected")
        got[name] = data
    for name, data in got.items():
        tmp = folder / (name + ".part")
        tmp.write_bytes(data)
        tmp.replace(folder / name)
    return {name: len(data) for name, data in got.items()}


class OnnxEngine:
    """YuNet finds the faces, SFace fingerprints them: two small networks run with onnxruntime on the CPU. They are decoded
    here in numpy (OpenCV is not needed), and every number below was checked against the model files themselves."""

    tag = "yunet-2023mar+sface-2021dec"

    def __init__(self, folder, threads: int = 2):
        from pathlib import Path

        import onnxruntime as ort

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = max(1, int(threads))
        opts.inter_op_num_threads = 1
        opts.log_severity_level = 3
        opts.add_session_config_entry("session.intra_op.allow_spinning", "0")     # idle threads sleep instead of burning the CPU
        folder = Path(folder)
        self._det = ort.InferenceSession(str(folder / DETECTOR), opts, providers=["CPUExecutionProvider"])
        self._rec = ort.InferenceSession(str(folder / RECOGNISER), opts, providers=["CPUExecutionProvider"])
        self._det_in = self._det.get_inputs()[0].name
        shape = self._det.get_inputs()[0].shape
        self._side = int(shape[2]) if isinstance(shape[2], int) and shape[2] == shape[3] else 0     # 640: the model's one size
        self._det_out = [o.name for o in self._det.get_outputs()]
        self._rec_in = self._rec.get_inputs()[0].name

    def _run(self, bgr: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """One pass of YuNet over a patch (rows x columns x BGR, 0..255): boxes (x, y, w, h), scores, five points each."""
        h, w = bgr.shape[:2]
        side = self._side or max(-(-h // 32) * 32, -(-w // 32) * 32)      # the model takes one fixed size (a multiple of 32)
        blob = np.zeros((1, 3, side, side), dtype=np.float32)
        blob[0, :, :h, :w] = bgr.transpose(2, 0, 1)
        named = dict(zip(self._det_out, self._det.run(None, {self._det_in: blob})))
        boxes, scores, points = [], [], []
        for stride in (8, 16, 32):
            cls, obj = named[f"cls_{stride}"][0, :, 0], named[f"obj_{stride}"][0, :, 0]
            bbox, kps = named[f"bbox_{stride}"][0], named[f"kps_{stride}"][0]
            cols = side // stride
            idx = np.arange(cls.shape[0])
            r, c = idx // cols, idx % cols
            score = np.sqrt(np.clip(cls, 0, 1) * np.clip(obj, 0, 1))
            keep = score >= CANDIDATE
            if not keep.any():
                continue
            r, c, bbox, kps, score = r[keep], c[keep], bbox[keep], kps[keep], score[keep]
            cx, cy = (c + bbox[:, 0]) * stride, (r + bbox[:, 1]) * stride
            bw, bh = np.exp(bbox[:, 2]) * stride, np.exp(bbox[:, 3]) * stride
            inside = (cx < w) & (cy < h)                        # not something found in the padding
            boxes.append(np.stack([cx - bw / 2, cy - bh / 2, bw, bh], axis=1)[inside])
            scores.append(score[inside])
            points.append(np.stack([np.stack([(kps[:, 2 * n] + c) * stride, (kps[:, 2 * n + 1] + r) * stride], axis=1)
                                    for n in range(5)], axis=1)[inside])
        if not boxes:
            return np.zeros((0, 4)), np.zeros(0), np.zeros((0, 5, 2))
        return np.vstack(boxes), np.concatenate(scores), np.vstack(points)

    def passes(self, w: int, h: int) -> list[tuple[float, int, int, int, int]]:
        """How a picture is looked at: (scale, left, top, width, height) of each patch. The model sees one fixed square, so a
        big picture is looked at once whole and shrunk (close faces) and then in overlapping squares at its own size (small
        faces); a face wholly inside any of them is found."""
        side = self._side
        if not side or max(w, h) <= side:
            return [(1.0, 0, 0, w, h)]

        def starts(n: int) -> list[int]:        # evenly spaced, overlapping by at least TILE_OVERLAP, the last at the edge
            count = 1 if n <= side else -(-(n - side) // (side - TILE_OVERLAP)) + 1
            return [round(i * (n - side) / (count - 1)) for i in range(count)] if count > 1 else [0]

        out = [(side / max(w, h), 0, 0, w, h)]
        out += [(1.0, x, y, min(side, w - x), min(side, h - y)) for y in starts(h) for x in starts(w)]
        return out

    def detect(self, image: Image.Image) -> list[Detection]:
        image = image.convert("RGB")
        w, h = image.size
        boxes, scores, points = [], [], []
        for scale, x0, y0, pw, ph in self.passes(w, h):
            if scale < 1.0:
                patch = image.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.Resampling.BILINEAR)
            else:
                patch = image.crop((x0, y0, x0 + pw, y0 + ph))
            bgr = np.asarray(patch, dtype=np.float32)[:, :, ::-1]
            b, sc, pt = self._run(np.ascontiguousarray(bgr))
            if not len(sc):
                continue
            b, pt = b / scale, pt / scale
            b[:, 0] += x0
            b[:, 1] += y0
            pt[:, :, 0] += x0
            pt[:, :, 1] += y0
            boxes.append(b)
            scores.append(sc)
            points.append(pt)
        if not boxes:
            return []
        boxes, scores, points = np.vstack(boxes), np.concatenate(scores), np.vstack(points)
        out = []
        for i in nms(boxes, scores):
            x, y, bw, bh = (float(v) for v in boxes[i])
            x0, y0 = max(0.0, x), max(0.0, y)
            x1, y1 = min(float(w), x + bw), min(float(h), y + bh)
            if x1 - x0 < 2 or y1 - y0 < 2:
                continue
            out.append(Detection((x0, y0, x1 - x0, y1 - y0), tuple((float(px), float(py)) for px, py in points[i]),
                                 float(scores[i])))
        return out

    def embed(self, image: Image.Image, detection: Detection) -> np.ndarray:
        face = np.asarray(align(image, detection.points), dtype=np.float32).transpose(2, 0, 1)[None]    # RGB, 0..255
        return self._rec.run(None, {self._rec_in: face})[0][0]
