"""Naming where a photo was taken, offline: the GPS position the camera wrote is matched to the nearest sizeable place in a
list of places (the gazetteer). Naming asks nothing of the internet; the list is a file on this PC, fetched once and only when you say so.

The list is GeoNames' "cities1000" (places over 1,000 people), kept as data/places/gazetteer.db (see `python -m
mary.photos.places --help`; it's a download of about 10 MB, from download.geonames.org under CC BY 4.0, and only fetched
when you say so). Without it MARY still reads photos, they just have no place name.

Which name: people say the town, not the neighbourhood and not the hamlet. Neighbourhoods ("sections of a populated place" in
GeoNames) are left out; among the places over 20,000 people within 25 km, the one that scores best on size less distance (a
tenfold bigger place is worth 8 km); with none, the nearest place; with nothing within 60 km (the sea, the desert), no place.
Checked on well-known public coordinates (see tests) with the real list: Paris, Sydney, Agra, Moscow, Cape Town...
"""
from __future__ import annotations

import argparse
import csv
import io
import math
import sqlite3
import zipfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np

EARTH_KM = 6371.0
NEAR_KM = 25.0
BIG = 20000
FAR_KM = 60.0
KM_PER_TENFOLD = 8.0       # a place ten times bigger is as good as one 8 km nearer
SECTION = "PPLX"           # GeoNames' code for a neighbourhood: never the name of a photo's place


@dataclass(frozen=True)
class Place:
    name: str
    region: str
    country: str
    lat: float
    lon: float
    km: float          # how far the photo's position is from it

    def label(self) -> str:
        return ", ".join(p for p in (self.name, self.region, self.country) if p)


def _unit(lat, lon) -> np.ndarray:
    la, lo = np.radians(lat), np.radians(lon)
    return np.stack([np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)], axis=-1)


class Gazetteer:
    def __init__(self, rows: list[tuple] = ()):
        """rows: (name, region, country, lat, lon, population[, feature code])."""
        from scipy.spatial import cKDTree

        self.rows = [tuple(r) + ("",) * (7 - len(r)) for r in rows]
        self.pop = np.array([r[5] for r in self.rows], dtype=np.int64)
        self.tree = cKDTree(_unit(np.array([r[3] for r in self.rows], dtype=np.float64),
                                  np.array([r[4] for r in self.rows], dtype=np.float64))) if self.rows else None

    @classmethod
    def open(cls, path: Path | None) -> "Gazetteer":
        """The list at `path`, or an empty one (no names) when it isn't there."""
        if not path or not Path(path).exists():
            return cls()
        con = sqlite3.connect(path)
        try:
            return cls(con.execute("SELECT name, region, country, lat, lon, population, feature FROM places").fetchall())
        except sqlite3.Error:
            return cls()
        finally:
            con.close()

    @property
    def available(self) -> bool:
        return self.tree is not None

    def nearest(self, lat: float, lon: float) -> Place | None:
        if self.tree is None:
            return None
        k = min(40, len(self.rows))
        chords, idx = self.tree.query(_unit(lat, lon), k=k)
        chords, idx = np.atleast_1d(chords), np.atleast_1d(idx)
        km = [float(x) for x in 2 * EARTH_KM * np.arcsin(np.minimum(chords / 2, 1.0))]
        found = [(d, int(i)) for d, i in zip(km, idx)]
        towns = [(d, i) for d, i in found if self.rows[i][6] != SECTION]       # not neighbourhoods
        big = [(d, i) for d, i in towns if d <= NEAR_KM and self.pop[i] >= BIG]
        if big:
            d, i = max(big, key=lambda x: math.log10(max(1, self.pop[x[1]])) - x[0] / KM_PER_TENFOLD)
        else:
            d, i = (towns or found)[0]
            if d > FAR_KM:
                return None
        name, region, country, la, lo = self.rows[i][:5]
        return Place(name, region, country, la, lo, round(d, 1))


# ---------------------------------------------------------------- building the list from GeoNames' files


def build(cities: str, countries: str, admin1: str, out: Path) -> int:
    """Write the gazetteer from GeoNames' own text files (cities1000.txt, countryInfo.txt, admin1CodesASCII.txt, given as
    their text). Returns how many places."""
    names = {}
    for line in countries.splitlines():
        if line and not line.startswith("#"):
            cols = line.split("\t")
            if len(cols) > 4:
                names[cols[0]] = cols[4]
    regions = {}
    for line in admin1.splitlines():
        cols = line.split("\t")
        if len(cols) > 1:
            regions[cols[0]] = cols[1]
    rows = []
    for cols in csv.reader(io.StringIO(cities), delimiter="\t", quoting=csv.QUOTE_NONE):
        if len(cols) < 15:
            continue
        try:
            lat, lon, pop = float(cols[4]), float(cols[5]), int(cols[14] or 0)
        except ValueError:
            continue
        rows.append((cols[1], regions.get(f"{cols[8]}.{cols[10]}", ""), names.get(cols[8], cols[8]), lat, lon, pop, cols[7]))
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    con.execute("CREATE TABLE places(name TEXT, region TEXT, country TEXT, lat REAL, lon REAL, population INTEGER, "
                "feature TEXT)")
    con.executemany("INSERT INTO places VALUES (?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()
    tmp.replace(out)
    return len(rows)


GEONAMES = "https://download.geonames.org/export/dump/"
DOWNLOAD_FILES = ("cities1000.zip", "countryInfo.txt", "admin1CodesASCII.txt")
DOWNLOAD_MB = 11.3        # what the three files come to (11,087,375 + 32,112 + 151,510 bytes when last checked)
ATTRIBUTION = "Place names: GeoNames (https://www.geonames.org), licensed CC BY 4.0.\n"


def _fetch(url: str) -> bytes:
    import httpx

    r = httpx.get(url, timeout=120, follow_redirects=True)
    r.raise_for_status()
    return r.content


def download(out: Path, fetch=None) -> int:
    """Fetch GeoNames' place list (about 11 MB, free, CC BY 4.0) and build the gazetteer at `out`. Returns how many
    places. This is the only network request photos ever make, and it only happens when asked for."""
    fetch = fetch or _fetch
    got = {name: fetch(GEONAMES + name) for name in DOWNLOAD_FILES}
    source = Path(out).parent / "source"      # kept, so the list can be rebuilt offline if its shape ever changes
    source.mkdir(parents=True, exist_ok=True)
    for name, data in got.items():
        (source / name).write_bytes(data)
    Path(out).parent.joinpath("ATTRIBUTION.txt").write_text(ATTRIBUTION, encoding="utf-8")
    return rebuild(out)


def rebuild(out: Path) -> int:
    """Build the gazetteer again from the files `download` kept (no internet). Returns how many places."""
    source = Path(out).parent / "source"
    with zipfile.ZipFile(source / "cities1000.zip") as z:
        cities = z.read("cities1000.txt").decode("utf-8")
    return build(cities, (source / "countryInfo.txt").read_text(encoding="utf-8"),
                 (source / "admin1CodesASCII.txt").read_text(encoding="utf-8"), out)


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m mary.photos.places", description=__doc__.split("\n\n")[0])
    parser.add_argument("--download", action="store_true", help=f"fetch GeoNames' place list (about {DOWNLOAD_MB} MB)")
    parser.add_argument("--yes", action="store_true", help="don't ask first")
    parser.add_argument("--to", type=Path, help="where to write it (default: data/places/gazetteer.db)")
    args = parser.parse_args()
    if not args.download:
        parser.print_help()
        return
    from mary.config import PROJECT_DIR

    out = args.to or PROJECT_DIR / "data" / "places" / "gazetteer.db"
    print(f"This downloads about {DOWNLOAD_MB} MB from download.geonames.org (cities over 1,000 people; CC BY 4.0) to {out}.")
    if not args.yes and input("Go ahead? [y/N] ").strip().lower() not in ("y", "yes"):
        print("Nothing downloaded.")
        return
    print(f"{download(out):,} places written.")



def km_between(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * EARTH_KM * math.asin(math.sqrt(a))


if __name__ == "__main__":
    main()
