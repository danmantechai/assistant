"""Describing a photo by vision (Phase 4): faces first, scene and thing tags, never naming or guessing a person.

Before a picture goes to the vision model, the usable faces found in Phase 3 (`photo_faces`) are drawn onto a working
copy as numbered boxes `[1]`, `[2]`, … ordered left to right. The vision model writes e.g. `"[1] blowing out candles"`,
and code strips any unnumbered person name or title the model might try to invent. Names come ONLY from `people`
(`state='named'`) when `[1]`, `[2]` are resolved into `photos.seen`.

Runs only while the PC is idle (`idle.input_idle_seconds()`) and plugged in (`not idle.on_battery()`), at low thread
priority, resuming where it stopped. A cloud vision model is opt-in only (`photos.vision.allow_cloud`).
"""
from __future__ import annotations

import io
import json
import logging
import re
from dataclasses import dataclass
from typing import Callable, Iterable
from urllib.parse import urlparse

from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger(__name__)

MAX_VISION_SIDE = 960
MAX_TAGS = 12

# Words that may legitimately appear capitalized inside or at the start of a visual description without being a person's name.
_SAFE_CAPITALIZED = {
    "a", "an", "the", "this", "that", "these", "those", "there", "here",
    "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
    "several", "many", "some", "both", "all", "no", "nobody", "someone", "people", "person",
    "group", "crowd", "child", "children", "baby", "toddler", "adult", "adults", "friends", "family", "couple",
    "man", "woman", "boy", "girl", "men", "women", "boys", "girls",
    "red", "blue", "green", "yellow", "pink", "orange", "purple", "white", "black", "brown", "grey", "gray",
    "gold", "silver", "bright", "dark", "light", "pale", "warm", "cool", "colorful", "colourful",
    "small", "large", "big", "tall", "short", "old", "new", "young", "wooden", "stone", "brick", "glass", "metal",
    "sunny", "cloudy", "rainy", "snowy", "foggy", "windy", "clear", "overcast",
    "indoor", "indoors", "outdoor", "outdoors", "inside", "outside", "close", "closeup", "close-up", "wide",
    "view", "scene", "photo", "picture", "shot", "image", "portrait", "landscape",
    "beach", "coast", "sea", "ocean", "lake", "river", "water", "harbour", "harbor", "bay", "pier", "dock", "shore",
    "mountain", "mountains", "hill", "hills", "valley", "forest", "woods", "tree", "trees", "grass", "field",
    "garden", "park", "flower", "flowers", "plant", "plants", "leaf", "leaves", "sand", "rock", "rocks", "snow", "ice",
    "sky", "clouds", "cloud", "sun", "sunset", "sunrise", "night", "day", "morning", "afternoon", "evening", "dusk", "dawn",
    "spring", "summer", "autumn", "fall", "winter", "christmas", "easter", "halloween", "birthday", "wedding", "party",
    "house", "home", "building", "buildings", "street", "road", "path", "bridge", "church", "castle", "tower", "shop", "cafe",
    "room", "kitchen", "living", "dining", "bedroom", "hall", "porch", "balcony", "yard", "patio",
    "table", "chair", "chairs", "sofa", "couch", "bed", "desk", "window", "door", "wall", "floor",
    "cake", "candles", "candle", "food", "plate", "plates", "cup", "cups", "glass", "glasses", "bottle", "bowl", "meal", "dinner", "lunch", "breakfast",
    "car", "cars", "bus", "train", "bicycle", "bike", "boat", "boats", "ship", "plane", "truck", "van",
    "dog", "dogs", "cat", "cats", "bird", "birds", "horse", "horses", "fish", "animal", "animals",
    "book", "books", "sign", "banner", "ball", "balloon", "balloons", "gift", "gifts", "present", "presents",
    "hat", "coat", "dress", "shirt", "suit", "glasses", "umbrella", "bag", "camera", "phone", "guitar", "piano",
    "standing", "sitting", "walking", "running", "smiling", "laughing", "looking", "holding", "playing", "eating",
    "drinking", "dancing", "talking", "reading", "blowing", "riding", "swimming", "cooking", "waving", "posing",
    "next", "near", "beside", "behind", "front", "under", "over", "around", "between", "with", "without", "by", "on", "in", "at",
}

# Titles or kinship words that try to identify who someone is rather than using face boxes.
_PERSON_TITLES = re.compile(
    r"\b(?:(?:a|an|the)\s+(?:man|woman|boy|girl|person|child|lady|gentleman)\s+)?(?:named|called)\s+[A-Z][\w'-]*|"
    r"\b(?:Mr|Mrs|Ms|Miss|Dr|Prof|Aunt|Auntie|Uncle|Grandma|Grandpa|Granny|Nana|Granddad|Grandfather|Grandmother|"
    r"Mum|Mom|Mama|Dad|Daddy|Papa|Mother|Father|Sister|Brother|Cousin)\b\.?(?:\s+[A-Z][\w'-]*)?",
    re.IGNORECASE,
)

_BOX_TOKEN = re.compile(r"\[(\d+)\]")
_WORD_RE = re.compile(r"\b([A-Za-z][A-Za-z'-]*)\b")


@dataclass
class SeenResult:
    raw: str                # sanitized template using only [1], [2], ... and never person names
    face_ids: list[int]     # photo_faces.id for [1], [2], ...
    tags: list[str]         # lowercase scene/thing tags, free of person names
    model: str              # tag of the vision model that produced it


def is_cloud_endpoint(model: str, host: str = "http://127.0.0.1:11434") -> bool:
    """True when `model` runs in the cloud (`:cloud` / `-cloud`) or `host` is not this PC."""
    m = (model or "").strip().lower()
    if m.endswith((":cloud", "-cloud")):
        return True
    try:
        hostname = (urlparse(host).hostname or "").lower()
    except ValueError:
        return True
    return hostname not in ("127.0.0.1", "localhost", "::1", "")


def _face_coords(f: dict) -> tuple[float, float, float, float]:
    if "box" in f and f["box"] is not None:
        bx, by, bw, bh = f["box"]
        return float(bx), float(by), float(bw), float(bh)
    return float(f["x"]), float(f["y"]), float(f["w"]), float(f["h"])


def order_faces(faces: list[dict]) -> list[dict]:
    """Order faces strictly left to right (`x`, then `y`, then `id`)."""
    return sorted(faces, key=lambda f: (_face_coords(f)[0], _face_coords(f)[1], int(f.get("id", 0))))


def draw_face_boxes(image: Image.Image, faces: list[dict]) -> Image.Image:
    """Draw numbered boxes `[1]`, `[2]`, … on a copy of `image` for each face in `faces` (ordered left to right).
    Returns the new `Image.Image` copy. Never modifies `image`."""
    ordered = order_faces(faces)
    im = image.copy().convert("RGB")
    w, h = im.size
    draw = ImageDraw.Draw(im)
    font = ImageFont.load_default()
    for idx, f in enumerate(ordered, start=1):
        fx, fy, fw, fh = _face_coords(f)
        x0 = max(0, min(w - 2, int(round(fx * w))))
        y0 = max(0, min(h - 2, int(round(fy * h))))
        x1 = max(x0 + 2, min(w - 1, int(round((fx + fw) * w))))
        y1 = max(y0 + 2, min(h - 1, int(round((fy + fh) * h))))
        # High-contrast double outline so the box stands out on light or dark backgrounds
        draw.rectangle([x0, y0, x1, y1], outline=(0, 0, 0), width=4)
        draw.rectangle([x0 + 1, y0 + 1, x1 - 1, y1 - 1], outline=(255, 220, 40), width=2)
        tag = f"[{idx}]"
        try:
            bbox = draw.textbbox((0, 0), tag, font=font)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        except Exception:
            tw, th = len(tag) * 7, 11
        bx0 = x0
        by0 = max(0, y0 - th - 6)
        draw.rectangle([bx0, by0, bx0 + tw + 8, by0 + th + 6], fill=(0, 0, 0), outline=(255, 220, 40), width=1)
        draw.text((bx0 + 4, by0 + 2), tag, fill=(255, 220, 40), font=font)
    return im


def prompt_for(n_faces: int) -> str:
    """The prompt sent with the photo to the vision model."""
    if n_faces > 0:
        boxes = ", ".join(f"[{i}]" for i in range(1, n_faces + 1))
        return (
            f"The people in this photo are marked with numbered boxes: {boxes}.\n"
            "Describe what the photo shows in one short sentence, and list 3 to 6 short scene or object tags.\n"
            f"Refer to any boxed person ONLY by their box number ({boxes}), for example \"[1] blowing out candles\".\n"
            "NEVER write, guess, or invent any person's name or family title.\n"
            'Reply with JSON only: {"description": "...", "tags": ["...", "..."]}'
        )
    return (
        "Describe what this photo shows in one short sentence, and list 3 to 6 short scene or object tags.\n"
        "NEVER write, guess, or invent any person's name or family title.\n"
        'Reply with JSON only: {"description": "...", "tags": ["...", "..."]}'
    )


def _parse_model_reply(reply: str) -> tuple[str, list[str]]:
    """Extract `(description, tags)` from JSON or plain text."""
    text = (reply or "").strip()
    if not text:
        return "", []
    # Strip markdown code fences if present
    fenced = re.search(r"\{.*\}", text, re.DOTALL)
    if fenced:
        try:
            obj = json.loads(fenced.group(0))
            if isinstance(obj, dict):
                desc = str(obj.get("description") or obj.get("caption") or obj.get("scene") or "").strip()
                raw_tags = obj.get("tags") or []
                if isinstance(raw_tags, str):
                    raw_tags = [t.strip() for t in raw_tags.split(",")]
                tags = [str(t).strip() for t in raw_tags if str(t).strip()]
                return desc, tags
        except (ValueError, TypeError):
            pass
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    desc = lines[0] if lines else ""
    desc = re.sub(r"^(?:description|scene|caption)\s*:\s*", "", desc, flags=re.I)
    tags: list[str] = []
    for ln in lines[1:]:
        m = re.match(r"^(?:tags|keywords|objects)\s*:\s*(.+)$", ln, flags=re.I)
        if m:
            tags.extend(t.strip(" -•*[]\"'") for t in m.group(1).split(",") if t.strip())
    return desc, tags


def sanitize_description(raw: str, n_faces: int, forbidden_names: Iterable[str] = ()) -> str:
    """Guarantee that `raw` contains no person names or titles, and only valid face box tokens `[1]..[n_faces]`."""
    s = " ".join(str(raw or "").split())
    if not s:
        return ""
    forbidden = {w.lower() for name in forbidden_names for w in re.findall(r"[A-Za-z][A-Za-z'-]*", str(name)) if len(w) >= 2}

    # 1. Replace out-of-range [k] tokens with "someone"
    def fix_box(m: re.Match) -> str:
        idx = int(m.group(1))
        return f"[{idx}]" if 1 <= idx <= n_faces else "someone"

    s = _BOX_TOKEN.sub(fix_box, s)

    # 2. Replace explicit titles / kinship / "named X"
    s = _PERSON_TITLES.sub("someone", s)

    # 3. Replace any forbidden names or capitalized proper nouns that are not in _SAFE_CAPITALIZED
    def fix_word(m: re.Match) -> str:
        word = m.group(1)
        low = word.lower()
        if low in forbidden:
            return "someone"
        if word[0].isupper() and low not in _SAFE_CAPITALIZED:
            return "someone"
        return word

    s = _WORD_RE.sub(fix_word, s)

    # Collapse accidental adjacent "someone someone"
    s = re.sub(r"\bsomeone(?:\s+someone)+\b", "someone", s, flags=re.I)
    s = " ".join(s.split()).strip(" .,;-")
    return s[:300]


def sanitize_tags(raw_tags: Iterable[str], forbidden_names: Iterable[str] = ()) -> list[str]:
    """Keep only clean scene/object tags; never let a person's name, title, or `[1]` box token into tags."""
    forbidden = {w.lower() for name in forbidden_names for w in re.findall(r"[A-Za-z][A-Za-z'-]*", str(name)) if len(w) >= 2}
    out: list[str] = []
    seen: set[str] = set()
    for raw in raw_tags or ():
        t = _BOX_TOKEN.sub("", str(raw or "")).strip(" .,;:-•*\"'[]()")
        if not t or _PERSON_TITLES.search(t):
            continue
        words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'-]*", t)
        if not words or len(words) > 4:
            continue
        # Reject any tag containing a forbidden name or an unknown capitalized proper noun
        bad = False
        for w in words:
            low = w.lower()
            if low in forbidden or low in ("someone", "person", "people", "man", "woman", "boy", "girl"):
                bad = True
                break
            if w[0].isupper() and low not in _SAFE_CAPITALIZED:
                bad = True
                break
        if bad:
            continue
        clean = " ".join(w.lower() for w in words)
        if len(clean) >= 2 and clean not in seen:
            seen.add(clean)
            out.append(clean)
        if len(out) >= MAX_TAGS:
            break
    return out


def resolve_description(seen_raw: str, face_ids: list[int], names_by_face: dict[int, str]) -> str:
    """Turn `seen_raw` (with `[1]`, `[2]`) into a human sentence using ONLY the user-given names in `names_by_face`.
    Unnamed faces become 'someone'."""
    if not seen_raw:
        return ""

    def sub_box(m: re.Match) -> str:
        idx = int(m.group(1)) - 1
        if 0 <= idx < len(face_ids):
            name = (names_by_face.get(int(face_ids[idx])) or "").strip()
            if name:
                return name
        return "someone"

    s = _BOX_TOKEN.sub(sub_box, seen_raw)
    s = re.sub(r"\bsomeone(?:\s+someone)+\b", "someone", s, flags=re.I)
    return " ".join(s.split()).strip()


def describe_image(
    image: Image.Image,
    faces: list[dict],
    ask_fn: Callable,
    model_tag: str = "vision-1",
    forbidden_names: Iterable[str] = (),
) -> SeenResult:
    """Draw numbered face boxes on `image`, ask `ask_fn`, and sanitize the result."""
    ordered = order_faces(faces)
    face_ids = [int(f["id"]) for f in ordered]
    boxed = draw_face_boxes(image, ordered)
    if max(boxed.size) > MAX_VISION_SIDE:
        boxed = boxed.copy()
        boxed.thumbnail((MAX_VISION_SIDE, MAX_VISION_SIDE), Image.Resampling.LANCZOS)
    prompt = prompt_for(len(face_ids))
    try:
        reply = ask_fn(boxed, prompt, model=model_tag)
    except TypeError:
        reply = ask_fn(boxed, prompt)
    raw_desc, raw_tags = _parse_model_reply(reply)
    clean_raw = sanitize_description(raw_desc, len(face_ids), forbidden_names=forbidden_names)
    clean_tags = sanitize_tags(raw_tags, forbidden_names=forbidden_names)
    return SeenResult(raw=clean_raw, face_ids=face_ids, tags=clean_tags, model=model_tag)
