# Photos in MARY's memory (Phase 2: sources and facts)

You add folders on **Settings › Memory › Photos**. MARY reads each photo once, on this PC, into a record in her memory store
(`user/memory/memory.db`) and keeps a small thumbnail (`user/memory/photos/thumbs/`). **Your originals are never moved,
changed or deleted**, and nothing is read until you add a folder. What she does with the records in conversation ("remember
our trip to the beach?") comes in later phases. Finding the faces and learning who is who is [people.md](people.md) (off until you switch it on).

## What she reads, and how she knows

| What | How | When it isn't there |
|---|---|---|
| **When it was taken** | the camera's own date (EXIF; HEIC through Windows), else a date in the file's name (`IMG_20231225_101500`, `WhatsApp Image 2023-12-25 at 10.15.00`, `Screenshot_20240115-093012`…), else the album's name ("Christmas 2019" = December 2019, "Summer 2015" = 2015, "Scans 1990s" = the 1990s), else the file's own date | no date; the page says so |
| **Where** | the GPS position the camera wrote, named from a list of places on this PC (nearest town people would name, not the neighbourhood) | no place; you can type one |
| **Copies** | the same bytes, or the same picture (a 64-bit difference hash within 3 bits, the same shape, no quarrel between the cameras' own times) | kept once, with all its files |
| **Words in it** | Windows' own OCR, on this PC | nothing kept |

Every date says **where it came from and how precise it is**. The camera's date and a date in a file's name are facts. An
album's name and the file's own date are **guesses**, and the page and MARY say so ("a guess", "I think"). When two sources
disagree (a camera date of 2024 in an album called "Christmas 2019": a print photographed later) the photo is marked
**needs a look** and neither is trusted.

## What you can fix

- a photo's date or place, a caption, or whole selections at once; "back to what was read" undoes it
- a whole **folder** ("this album is 1985", "these were all at Home town"), for the photos whose date is a guess or for every
  photo in it; it also reaches photos added to that folder later
- "these are the same photo" for look-alikes MARY didn't merge on her own (she never merges on a loose match)
- **Remove** a photo or a folder from MARY's records for good: the record, its thumbnail and the words read from it. The
  originals stay, and a photo you removed is not added again when the folder is scanned.

## What is set aside (counted, never read)

- images under 200 px (icons, stickers) and files that can't be read (damaged, empty, too big): listed with the reason
- **OneDrive "files on demand"** that are online only: reading one would download it, so they are listed and never opened
- hidden and system folders, thumbnail caches, links and junctions, MARY's own screenshot folder

## How it runs

A worker looks after your folders on its own thread, at below-normal priority, and steps aside while MARY is answering you.
It scans at start and every 5 minutes (by name, size and time), so new photos arrive on their own. Every stage picks up where it
stopped. If you open Settings on its own while MARY isn't running, reading only continues while that window is open.

Measured on this laptop (Ryzen 5 PRO 4650U, no GPU), 500 invented 12-megapixel photos of 3.4 MB each (1.7 GB):
scan 0.02 s; quick facts (read, thumbnail, fingerprints, copies, date, place) **44 s**, 88 ms a photo; words 136 s, 0.3 s a photo;
**3 minutes in all**. 454 photos came out of 500 files, exactly the number expected (46 copies kept once, none merged
wrongly). Thumbnails 3 MB, database under 1 MB, 78 MB of RAM. Reproduce with `python -m mary.photos.bench`.

## Place names (the one download)

GPS positions are named offline from GeoNames' "cities1000" list (places over 1,000 people, free, **CC BY 4.0**: place names
from [geonames.org](https://www.geonames.org)). It is 11.3 MB, kept in `data/places/` (never published), and fetched only
when you say so: the **Get the list of places…** button on the Photos page, or `python -m mary.photos.places --download`. It is the only
network request photos ever make, and only the list comes to your PC. Lookup takes 0.08 ms. Checked on well-known public
places with the real list: the Eiffel Tower is Paris, Big Ben is London, the Statue of Liberty is New York City, Table Mountain is Cape Town,
Cape Coast Castle is Cape Coast, the open sea has no name.

## Privacy

Photos, thumbnails and the words read from them stay on this PC. **Logs hold counts and timings only**, never a file name, a
place or text (`logs/photos.jsonl`). OCR can read a document someone photographed: that text is searchable here, never logged;
turn it off with **Read the words in photos** (`photos.read_text`). Removing a photo removes its words from the database and
the search index for good.

## For developers

`mary/photos/`: `library.py` (**the API**: `PhotoLibrary`; nothing else touches the tables), `ingest.py` (the worker),
`sources.py` (folders and the scan), `read.py` (decode, camera notes, thumbnail, fingerprints), `dates.py`, `copies.py`,
`places.py`, `text.py`, `synth.py` (invented pictures with known answers: the hard cases for the tests and the benchmark),
`bench.py`; the faces are `faces.py`, `cluster.py` and `people.py` (see [people.md](people.md)). The tables are migration 2 of `memory.db` (`mary/memory/db.py`): `photo_folders`, `photos`, `photo_files`,
`photo_ignored`, `photos_fts`, every row with an owner and library id and a format number. The settings page talks to it through
`mary/settings/photos.py` and `/api/photos/...` (thumbnails at `/thumb/<id>`, served only to this page).
