"""Photos in MARY's memory (Phase 2: sources and facts).

You add folders; MARY reads each photo once, with code, into a record in the memory store: when it was taken and how she
knows (read.py, dates.py), where (places.py), whether it's a copy of another (copies.py), the words in it (text.py),
and a small thumbnail. Originals are never moved, changed or deleted. library.py is the API everything else uses;
ingest.py is the worker that looks after the folders; synth.py makes the invented pictures the tests and the benchmark use.

Later phases add faces, a description of what is happening, moments, and what she says about them.
"""
from __future__ import annotations

ENABLED = True   # the tests switch it off (tests/conftest.py) and turn it on where they test it

# The shipped defaults (config.yaml's photos: section explains each); a missing key falls back to these.
DEFAULTS = {
    "enabled": True,
    "min_side": 200,         # smaller than this on its short side is an icon or a sticker, not a photo
    "read_text": True,       # the words printed in a photo (Windows OCR, on this PC)
    "rescan_s": 300,         # look for new photos in your folders this often
    "gazetteer": "{project}/data/places/gazetteer.db",   # the list of places GPS positions are named from
    "exclude": [],           # folders never to look in (MARY's own screenshots are always left out)
    # Faces (Phase 3). On by default (the models still have to be fetched on the People page); "allowed: false" turns it off.
    "faces": {
        "allowed": True,                                    # false: faces are never read
        "models": "{project}/data/models/faces",            # where the two face models live (fetched on the People page)
        "detect_px": 1600,       # a picture is looked at no bigger than this on its long side
        "min_px": 48,            # a face smaller than this is not fingerprinted
        "min_score": 0.7,        # nor one the detector is less sure of than this
        "threads": 2,
        # How alike (cosine of fingerprints) decides. A wrong name is worse than a question, so these are cautious.
        "join": 0.55,            # this like a person you named, and clearly ahead of the others: it is them
        "margin": 0.08,          # "clearly ahead": by this much
        "ask": 0.40,             # from here to `join`: possibly them, so it asks
        "group": 0.45,           # unnamed groups this alike (on average) are one
        "alone": 0.05,           # a single face needs this much more to join a group
        "same": 0.30,            # two groups this alike are offered as "the same person?"
    },
}


def open_library(cfg, store):
    """The photo library for this config on this memory store, or None when photos are switched off."""
    from pathlib import Path

    from mary.config import expand_path
    from mary.memory import merged
    from mary.photos.library import PhotoLibrary
    from mary.photos.places import Gazetteer

    settings = dict((cfg.get("photos") if hasattr(cfg, "get") else None) or {})
    s = merged(DEFAULTS, settings)
    if not ENABLED or not s["enabled"] or store is None:
        return None
    exclude = [str(expand_path(p)) for p in s["exclude"]]
    shots = (cfg.get("paths") or {}).get("screenshots") if hasattr(cfg, "get") else None
    if shots:
        exclude.append(str(expand_path(shots)))
    settings["exclude"] = exclude
    path = Path(expand_path(s["gazetteer"]))
    lib = PhotoLibrary(store, settings, Gazetteer.open(path))
    lib.gazetteer_path = path
    return lib
