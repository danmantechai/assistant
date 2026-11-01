# People in your photos (Phase 3)

On **Settings › Memory › People**, MARY can find the faces in your photos, group the ones that look like the same person, and
learn who they are **from you**. Then she can find every photo of someone, and later talk about them. It is **on by default** (it starts once the two models are fetched; there is no switch on the page). A face fingerprint
is biometric data, so everything stays on this PC and **Delete all face data** turns it off.

**The one rule: a name is only ever one you typed.** MARY finds faces and piles them up by resemblance; a pile has no name until
you give it one. Nothing guesses, infers or fills in a name, and a doubtful match is *asked about*, not decided.

## How it goes

1. **Fetch the models.** The page asks you to fetch two small face models (about 39 MB, once; see below), then reads your photos for
   faces in the background, a photo at a time, after the quick facts and taking turns with the words in photos.
2. **Name a group, once.** "Who are these?" shows piles of faces that look like one person. Type a name (and, if you like, how you know
   them) and every photo of them is found, now and for photos that arrive later.
3. **Answer the questions.** A face that might be someone you named (but is not alike enough to be sure) waits as **"Is this Ama?"**:
   pick the faces that are her and say yes, or no, and a no is remembered. Nothing is added to anyone until you say.
4. **Fix what she got wrong.**
   - **The same person?** Two piles that look alike but not alike enough to join for you, like someone as a child and sixty years
     later, are offered as a pair. Yes joins them; no is remembered. MARY never joins a named person to another.
   - **Split:** pick the faces in a pile that are someone else; they become a pile of their own and are not put back.
   - **Not someone I know:** strangers, a crowd, a poster: the pile is set aside, similar faces join it quietly and are never asked about.
   - Rename, add how you know them, take a name away (the faces stay together), join two people, move faces from one to another.
   - **Delete** a person: their name, every fingerprint and face picture and every link, for good (see Privacy).

The **Photos** page gets a **Person** filter, and a photo's dialog says who is in it ("In this photo: Ama, Kofi · 1 face not named
yet").

## How sure it has to be

Each face is fingerprinted (128 numbers) and compared by cosine with a few fingerprints picked to cover each person's different looks
(so someone across thirty years is compared with their nearest era, not an average that matches no photo). The cut-offs are cautious,
because a wrong name is worse than a question, and are settings under `photos.faces` in `config.yaml`:

| A face that is… | …is |
|---|---|
| ≥ 0.55 like one person you named, and clearly ahead (0.08) of everyone else, and no unnamed group is clearly closer | **recognised**: added to them |
| 0.40 to 0.55 like a person you named, or too close to call between two | **asked**: "Is this Ama?" |
| ≥ 0.50 like an unnamed group | joins that group; otherwise starts its own |
| in a photo with a face already in a group | never in that group (nobody is in a photo twice) |
| smaller than 48 px or one the detector is unsure of | kept as a box, **never fingerprinted, grouped or named** |

Groups that turn out to be one person (the order photos arrive in can leave someone in two piles) are merged when they are alike on
average, or when several distinct pairs of their faces are strongly alike (a bridge through the years between). Piles with a face
in the same photo are never merged. Whatever you decide (a name, a join, a split, "not them") is kept and never overruled by a later guess.

## Measured

Everything below is **real**: the real models, on this laptop (Ryzen 5 PRO 4650U, no GPU), with MARY itself running beside.

*How well they tell people apart.* A one-off check on 21 public-domain NASA portraits of 11 people (fetched to a scratch folder with
your OK, never in the repository or the tests): **21 of 21 faces found** (scores 0.93 to 0.96), one clear face in each.
Same person (12 pairs, 2 to 38 years apart): cosine **0.48 to 0.83, mean 0.69**; the long gaps were 18 years 0.82, 23 years 0.72,
36 years 0.57, 38 years 0.48. Different people (198 pairs): mean 0.12, 99th percentile 0.30, **highest 0.44**. At the published SFace
threshold of 0.363: 12 of 12 same-person pairs and 1 of 198 different pairs; at this page's 0.55: 11 of 12 and 0 of 198.
**Treat it as a sanity check, not a benchmark**: 21 studio portraits are the easy case (front-on, even light, adults), the numbers
come from few pairs, and family snapshots, children, babies, profiles, sunglasses and old scans will be harder. That is why
the cut-offs are cautious and why unsure matches are asked about. Reproduce on your own sample (a folder with one sub-folder per
person; it prints numbers and folder names only): `python -m mary.photos.bench --people FOLDER`.

*How fast.* A portrait of about 1000 × 1300 px takes 0.24 s from file to fingerprint. A 12-megapixel phone photo takes **0.63 s**
(decode 0.3 s; the detector takes one fixed 640 × 640 square, so a big picture is looked at once whole and then in overlapping squares: 7 to 10 looks);
each clear face adds 20 ms. For 5,000 photos that is under an hour in the background; for 50,000, most of a working day, at
below-normal priority, stopping while she is answering, picking up where it stopped. `detect_px` (default 1600) trades small
faces for speed. `python -m mary.photos.bench --faces` times it on invented photos.

*What the tests show.* The rules (grouping, recognising, asking, merging, splitting, the same person decades apart, look-alikes,
crowds, any order of photos, what survives a re-read) are tested on invented fingerprints with known answers; the whole path
(folder, faces, groups, name once, new photo recognised, doubtful one asked) with invented photos and a stand-in for the models;
the real models' mechanics (loading, alignment onto the standard 112 × 112 layout, no clear face in invented pictures, speed).
None of those say anything about how well a real model sees a real face: the portraits above do.

## The models (the one download)

Two small networks from [OpenCV's model zoo](https://github.com/opencv/opencv_zoo), run with onnxruntime on the CPU (no new package),
fetched only when you press **Get the face models…** and kept in `data/models/faces/` (never published). Their sizes and
hashes are checked, and match what Hugging Face publishes.

| Model | Does | Size | Licence |
|---|---|---|---|
| YuNet `face_detection_yunet_2023mar` | finds faces and five landmarks | 233 KB | MIT |
| SFace `face_recognition_sface_2021dec` | 128-number fingerprint of an aligned face | 38.7 MB | Apache-2.0 |

**A caveat for anything beyond your own PC.** The two files are MIT and Apache-2.0, but OpenCV does not say what SFace was trained
on beyond public research face sets, and I found no small model with clearer provenance (InsightFace's are non-commercial). A hosted
product would need its own licence review, a consent and likeness policy (it is other people's faces), and probably a different model.

## Privacy

- **On by default.** **Delete all face data…** removes every fingerprint,
  face picture, group and name and switches it off. `photos.faces.allowed: false` in `config.yaml` stops faces being read at all.
- Fingerprints are vectors in the same `memory.db` (owner and library ids on every row); face pictures are small files in
  `user/memory/photos/faces/`. Both stay on this PC, are **never sent to a model** and **never logged** (the log holds counts and timings).
- **Deleting a person deletes their fingerprints, pictures and links for good**: from the database (it overwrites what it frees),
  and from the copies MARY keeps before a format change (`user/backups/`), which are scrubbed too. The photos stay, and are not read
  for faces again unless you ask (**Look at every photo again**). Removing a photo or a folder removes its faces the same way.
- Face pictures are shown only to the settings page (the server checks who asks) and the browser is told **not to keep them**, so a
  deleted person does not linger in a browser's cache.
- The only network request is the optional fetch of the models (about 39 MB), which sends nothing of yours.

## For developers

`mary/photos/`: `faces.py` (an *engine* does `detect` and `embed`; `analyse` decides which faces are clear enough and makes the face
pictures; `OnnxEngine` is YuNet + SFace; `fetch` gets the models), `cluster.py` (the rules, pure numpy: `Gallery` of exemplars,
`decide`, `consolidate`), `people.py` (**the API**: `lib.people`; groups, names, questions, merge, split, delete for good),
`ingest.py` (the faces stage and the grouping work that follows a name), `synth.py` (`Cast`: invented people as fingerprints with known
looks, ages and look-alikes; `StandIn`: a stand-in engine that recognises invented pictures from a table), `bench.py`. The tables are
migration 3 of `memory.db`: `people`, `photo_faces`, `people_nots` (every "not them" and "not the same person"), and `photos.faces_model`
(which models have read a photo, so a new model reads every photo again and keeps what you told it). Fingerprints are space `face`
of table `photo_faces` in the vectors table. The settings page talks to it through `mary/settings/people.py` and `/api/people/...`
(face pictures at `/face/<id>`).
