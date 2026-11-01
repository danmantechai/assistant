"""How long photos take to go in: python -m mary.photos.bench [--n 500] [--size 4000x3000] [--no-text] [--faces]

Writes n invented photos (mary/photos/synth.py: never a real one) into a scratch folder, adds it as a source and times each
stage: the scan, the quick facts (read, thumbnail, fingerprints, copies, date, place), the words in the pictures and, with
--faces, the faces (the real models, if they are on this PC; invented pictures have no faces, so this is the cost of looking,
and each face found adds about 20 ms for its fingerprint). Reports the numbers and whether the right number of photos came out
(nothing counted twice).

How well the face models tell people apart is measured on pictures you give it:

    python -m mary.photos.bench --people FOLDER

FOLDER has one sub-folder per person, each with a few pictures of that person (the same person at different ages is the
interesting case). It reports how many faces were found, how alike the same person's faces are and how alike different
people's are, and what the cut-offs in config.yaml (photos.faces) would do with them. It prints folder names and numbers only.
"""
from __future__ import annotations

import argparse
import os
import shutil
import tempfile
import time
from pathlib import Path

import numpy as np
import psutil

from mary.memory.store import MemoryStore
from mary.photos import ingest, synth, text
from mary.photos.library import PhotoLibrary
from mary.photos.places import Gazetteer

PLACES = [("Harbourton", "Coastal Region", "Ghanaland", 5.1053, -1.2466, 90000),
          ("Northfield", "Greater North", "Britannia", 51.5072, -0.1276, 8_000_000),
          ("Kestrel Bay", "Cape Region", "Southland", -33.9249, 18.4241, 4_000_000)]


def run(n: int, size: tuple[int, int], read_text: bool = True, keep: bool = False, with_faces: bool = False) -> dict:
    scratch = Path(tempfile.mkdtemp(prefix="mary-photos-bench-"))
    try:
        root = scratch / "Photos"
        t = time.perf_counter()
        made = synth.bulk(root, n, size=size, grain=True)
        generated = time.perf_counter() - t
        store = MemoryStore(scratch / "user" / "memory" / "memory.db", backups=scratch / "b")
        lib = PhotoLibrary(store, {"read_text": False}, Gazetteer(PLACES))
        ing = ingest.Ingest(lib)
        lib.add_folder(root)
        proc = psutil.Process()
        t = time.perf_counter()
        report = ing.scan()
        scan_s = time.perf_counter() - t
        t = time.perf_counter()
        facts_units = ing.drain()
        facts_s = time.perf_counter() - t
        out = {"n": n, "size": f"{size[0]}x{size[1]}", "bytes": sum(p.stat().st_size for p in root.rglob("*") if p.is_file()),
               "generated_s": generated, "scan_s": scan_s, "found": report["found"], "facts_s": facts_s,
               "facts_units": facts_units, "status": lib.status(), "expected": made, "average_ms": ing.average_ms(),
               "thumbs_bytes": sum(p.stat().st_size for p in (lib.dir / "thumbs").rglob("*.jpg"))}
        if read_text and text.available():
            lib.settings["read_text"] = True
            t = time.perf_counter()
            out["text_units"] = ing.drain()
            out["text_s"] = time.perf_counter() - t
            out["average_ms"] = ing.average_ms()
        if with_faces:
            from mary.photos import faces

            engine = faces.load(lib)
            if engine is None:
                out["faces_missing"] = True
            else:
                ing._engine = engine
                lib.people.set_enabled(True)
                engine.detect(synth.picture(1, (640, 480)))        # the first call loads the models: not counted
                t = time.perf_counter()
                out["faces_units"] = ing.drain()
                out["faces_s"] = time.perf_counter() - t
                out["faces_found"] = lib.people.status(engine.tag)["faces"]
                out["average_ms"] = ing.average_ms()
        out["rss_mb"] = proc.memory_info().rss / 2**20
        out["db_mb"] = store.path.stat().st_size / 2**20
        store.close()
        return out
    finally:
        if not keep:
            shutil.rmtree(scratch, ignore_errors=True)


def people_report(folder: Path, engine=None) -> dict:
    """The real face models on a folder with one sub-folder of pictures per person: detection, and the cosine of the fingerprints
    of the same person's faces and of different people's. Only pictures with exactly one clear face are paired."""
    from mary.photos import faces, read

    folder = Path(folder)
    if engine is None:
        from mary.config import expand_path
        from mary.photos import DEFAULTS

        models = Path(expand_path(DEFAULTS["faces"]["models"]))
        if not faces.present(models):
            raise SystemExit("The face models are not on this PC yet (Settings > People > fetch them).")
        engine = faces.OnnxEngine(models, 2)
    pictures, none, many, kept, took = 0, 0, 0, [], 0.0
    for who in sorted(p for p in folder.iterdir() if p.is_dir()):
        for f in sorted(who.iterdir()):
            if f.suffix.lower() not in read.EXTENSIONS:
                continue
            pictures += 1
            try:
                t = time.perf_counter()
                found = faces.analyse(read.decode(f.read_bytes(), f.suffix, 1600).image, engine)
                took += time.perf_counter() - t
            except read.Unreadable:
                continue
            clear = [x for x in found if x.usable]
            none += not clear
            many += len(clear) > 1
            if len(clear) == 1:
                kept.append((who.name, clear[0].vector))
    same, diff = [], []
    for i in range(len(kept)):
        for j in range(i + 1, len(kept)):
            (a, va), (b, vb) = kept[i], kept[j]
            (same if a == b else diff).append(float(va @ vb))
    same, diff = np.array(same), np.array(diff)
    cuts = (0.30, 0.363, 0.40, 0.45, 0.55)
    return {"pictures": pictures, "no_clear_face": none, "several_clear_faces": many, "paired": len(kept),
            "ms_a_picture": took / max(1, pictures) * 1000, "same_pairs": len(same), "different_pairs": len(diff),
            "same": None if not len(same) else {"min": float(same.min()), "mean": float(same.mean()), "max": float(same.max())},
            "different": None if not len(diff) else {"mean": float(diff.mean()), "p99": float(np.percentile(diff, 99)),
                                                     "max": float(diff.max())},
            "same_at_or_above": {c: int((same >= c).sum()) for c in cuts},
            "different_at_or_above": {c: int((diff >= c).sum()) for c in cuts}}


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m mary.photos.bench")
    parser.add_argument("--n", type=int, default=500)
    parser.add_argument("--size", default="4000x3000", help="WIDTHxHEIGHT of each photo (a phone's is about 4000x3000)")
    parser.add_argument("--no-text", action="store_true")
    parser.add_argument("--faces", action="store_true", help="also time the faces stage (needs the face models)")
    parser.add_argument("--people", type=Path, metavar="FOLDER", help="measure the face models on a folder of one sub-folder per person")
    args = parser.parse_args()
    if args.people:
        r = people_report(args.people)
        print(f"{r['pictures']} pictures, {r['ms_a_picture']:.0f} ms each; no clear face in {r['no_clear_face']}, "
              f"several in {r['several_clear_faces']}; {r['paired']} used for pairs")
        print(f"same person, {r['same_pairs']} pairs: {r['same']}")
        print(f"different people, {r['different_pairs']} pairs: {r['different']}")
        print(f"same-person pairs at or above each cosine: {r['same_at_or_above']}")
        print(f"different-person pairs at or above each cosine: {r['different_at_or_above']}")
        return
    w, h = (int(x) for x in args.size.lower().split("x"))
    r = run(args.n, (w, h), not args.no_text, with_faces=args.faces)
    st = r["status"]
    print(f"{r['n']} invented photos at {r['size']}: {r['bytes'] / 2**20:.0f} MB on disk (made in {r['generated_s']:.0f} s)")
    print(f"  scan        {r['scan_s']:.2f} s   ({r['found']} files)")
    print(f"  quick facts {r['facts_s']:.1f} s   ({r['facts_s'] / max(1, r['facts_units']) * 1000:.0f} ms a file, end to end)")
    if "text_s" in r:
        print(f"  words       {r['text_s']:.1f} s   ({r['text_s'] / max(1, r['text_units']) * 1000:.0f} ms a photo)")
    if "faces_s" in r:
        print(f"  faces       {r['faces_s']:.1f} s   ({r['faces_s'] / max(1, r['faces_units']) * 1000:.0f} ms a photo, "
              f"{r['faces_found']} faces found)")
    elif r.get("faces_missing"):
        print("  faces       skipped: the face models are not on this PC (Settings > People)")
    print(f"  stage averages (ms): {r['average_ms']}")
    print(f"  came out: {st['photos']} photos from {st['files']['ok']} files ({st['copies']} copies merged); "
          f"expected {r['expected']['photos']} photos from {r['expected']['files']} files; "
          f"{st['files']['unreadable']} unreadable, {st['files']['tiny']} tiny")
    print(f"  thumbnails {r['thumbs_bytes'] / 2**20:.1f} MB, database {r['db_mb']:.1f} MB, process {r['rss_mb']:.0f} MB")


if __name__ == "__main__":
    main()
