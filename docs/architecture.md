# How MARY is built

MARY is built like Lego: a **core** that every ability relies on, and **skill bricks**, one ability each, that
you can add or remove without touching the core.

## The core

| Part | Where | Job |
|---|---|---|
| Ears | `mary/audio/` | the wake word or Ctrl+Space, the microphone and its clean-up (`clean.py`), speech to text: local Whisper (`stt.py`) or ElevenLabs Scribe, live (`live_stt.py`, `cloud_stt.py`) |
| Voice | `mary/audio/tts.py`, `cloud_tts.py` | speaking sentence by sentence while she's still thinking, numbers and dates said naturally; Kokoro locally or an ElevenLabs voice |
| Brain socket | `mary/brain/` | the one place a language model plugs in (`Brain`): `ollama.py` (a model on this PC, or Ollama's cloud models) and `api.py` (any OpenAI-compatible service) |
| Agent loop | `mary/llm/agent.py` | the model, native tool calling, optional thinking, the cache-friendly prompt layout |
| Memory | `mary/memory/`, `mary/profile.py`, the agent's history | what you told her to remember (`user/profile.yaml`), the diary and what she has learned (`user/memory/`), the conversation so far |
| Safety gate | `mary/tools/safety.py`, `registry.py` | checks every action against your words; asks only for risky steps |
| Brick slot | `mary/tools/registry.py`, `mary/skills/__init__.py` | loads the bricks named under `skills:` |
| Screen by layout | `mary/page.py` | any window as areas read in order (dividers found in its picture; UI Automation, OCR fill, or the page itself) |
| MARY's browser | `mary/web/` | Edge driven by Playwright, its own profile; pages read and clicked through their own structure |
| Vision | `mary/vision.py`, `skills/look.py` | a model that can see answers questions about a window or photo |
| Code first | `mary/net.py`, `mary/terminal.py`, `skills/calculate.py` | direct requests, PowerShell commands (look-only ones run at once), exact maths |
| Feedback loop | `mary/ready.py`, `registry.py` | around every tool call: the window it works on is ready first, what it changed has settled after (responds, title and a tiny picture steady), a "not yet" is tried again while that can help, and the result ends with what's really on screen. Waiting limits are learned per app from `logs/ready.jsonl` |
| Reflexes | `mary/reflexes.py` | the few instant commands: volume, brightness, pause/skip, lock, the time |
| Persona | `mary/persona.py`, `personas/` | who she is: character, speech, writing, voice, wake phrases, lore ([personas.md](personas.md)) |
| Settings | `mary/config.py` | `config.yaml` (defaults), then the persona's settings, then `user/config.yaml` (yours) over both |
| Settings page | `mary/settings/` | a local web page (127.0.0.1:8765) that edits those same files, comments kept: `store.py` (where each change goes), `schema.py` (what each page shows), `wake.py` (recording and training a wake word from the browser), `models.py` (adding and testing a model), `server.py` (stdlib HTTP + JSON API), `web/` |

## What happens when you speak

1. **Wake:** "Hey Mary" (a small model trained on your voice, double-checked by Whisper) or Ctrl+Space. She
   answers ("Yes?") or chimes.
2. **Listen:** the request is recorded until you pause (a short pause for short commands, up to 5 s once you've
   talked a while; hold the hotkey to talk as long as you like). It's cleaned like a browser cleans a mic.
3. **Hear:** local Whisper (started early during your pause), or ElevenLabs Scribe live: your words stream up
   while you talk and the text is ready about 0.3 s after you stop.
4. **Decide:**
   - quick replies ("thanks", "no", "bye") are answered at once
   - a request that is plainly one instant command ("volume up") is a **reflex**, done in under a second
   - everything else goes to the **brain whole**, with the conversation, and she answers in her own words,
     calling tools as needed; every action passes the safety gate
5. **Speak:** her reply is spoken as it's written, starting with the first phrase.

## The prompt, and why it's laid out this way

On a CPU, every token the model re-reads costs ~30 ms. Ollama reuses its cache only when a prompt continues the
previous one exactly, so the prompt is built to change only at its end:

```
[system prompt + tool schemas]        static: the persona's soul + speech, the rules, the bricks' guides (cached)
[what you asked MARY to remember]     a pinned first exchange; changes rarely
[the conversation so far]             append-only, trimmed only every few turns
[<context> time, front window, any lore, writing examples or remembered lines that fit + your request]   new each turn
```

Changing data inside the system prompt forced a full re-read (65–97 s). As a pinned exchange it costs one
short re-read. A background "prefill" after each request reads the conversation in while you're idle.

## Two brain modes

`brain.mode` picks how a request reaches the brain:
- **new** (the default): the flow above. The model is the brain.
- **today**: the older design, kept for comparison: a semantic router (`mary/router/`) matches requests by
  meaning against `intents.yaml`, splits sentences at "and", runs confident matches directly, and gives the
  model its guess for the rest.

Measured on 24 turns of real conversations (tools faked, `python -m mary.replay`): the new mode cut actions
nobody asked for from 15 to 0–3 and prepared sentences from 16 to 1, and her first words came in about 2 s
instead of 10 s. Its weakness with a 2B model: it sometimes answers without calling the tool it needed.

## Memory

One local store, `mary/memory/`, for what happened and what she has learned (and, in later phases, the moments in your
photos). `MemoryStore` (`store.py`) is the only way in: the agent, the memory brick and the settings page never touch
its SQL or its files, and every record carries an owner and a library id, so a phone app or a hosted version could sit
behind the same calls. The file is `user/memory/memory.db`: records, a keyword index (SQLite FTS5), and vectors by
space (`text` now; faces and pictures later), compared brute-force in a numpy matrix (11 ms for 50,000).

| Kind | What | Written by |
|---|---|---|
| core | what you told her to "remember that…": stays in `user/profile.yaml`, pinned in every request, and is searchable | you |
| diary | one line per request: your words, the skills used, what she said (never what a skill read out) | code, after each request |
| fact | something lasting you said ("My sister is Ama"), with its source line, importance, and an end date when replaced | the background pass, or you |
| reflection | a sentence about a finished day | the background pass |

- **Per request** (`recall.py`): facts and day summaries that match the words come along in the `<context>` block, the
  diary only when you ask about the past ("what did we do yesterday"). Nothing is added when nothing matches well, and
  a request never waits for it (if the meaning model is still loading the search is keywords only).
- **Search** (`search.py`): code reads the dates ("last Tuesday", `when.py`) and names; keywords and meaning (bge-small,
  the router's model) are merged into a relevance; recency and importance only break ties. Tuned on a made-up month
  (`python -m mary.memory.bench`).
- **Plain files** (`files.py`): `facts.md`, `diary/<day>.md` and `reflections.md` are the same memories as text.
  A line you change, add or take out is imported within minutes ("removed by hand" ends a memory, never erases it);
  delete `memory.db` and the files rebuild it.
- **Forgetting** ends a memory and keeps it with its date; only "delete it for good" removes one (asked first), from
  the store, its keywords, its vector and the files.
- **The background pass** (`consolidate.py`, off unless you switch it on): when nobody is using MARY or the PC the
  brain (`Brain.json`) reads your words from the new diary lines and proposes facts as JSON; code keeps only what its
  line really says, merges duplicates and ends what a new fact replaces.

## Photos

Phase 2 of the photo memory (see [photos.md](photos.md)): `mary/photos/`. You add folders; a worker thread reads each photo
once into a record in the same `memory.db` (migration 2: `photo_folders`, `photos`, `photo_files`, `photo_ignored`,
`photos_fts`, all with owner and library ids) and a thumbnail in `user/memory/photos/`. Code reads everything it can: the camera's
date and GPS (Pillow; HEIC through Windows' own decoder), a date in the file's name or the album's name, the nearest town from a
list of places on this PC, a difference hash that finds copies, and the words in the picture (Windows OCR). Every date says where it
came from and whether it is a guess. `PhotoLibrary` (`library.py`) is the API; the settings page reaches it through
`mary/settings/photos.py`. Originals are never touched.

## People

Phase 3 of the photo memory (see [people.md](people.md)). Off until the person switches it on (a flag in `memory.db`'s `meta`
table). The photo worker gets a **faces stage**: the picture is decoded the right way up (`read.decode`), `faces.analyse` runs an engine
(YuNet to find faces and landmarks, SFace to fingerprint an aligned face, both with onnxruntime) and keeps a fingerprint and a small
picture only for faces clear enough; `people.py` puts each face in a group by the rules in `cluster.py` (pure numpy: a `Gallery` of
exemplar fingerprints per group, `decide`, `consolidate`). Migration 3 adds `people`, `photo_faces` and `people_nots`; fingerprints are
vectors of space `face`. A name comes only from the person (`People.name`); doubtful matches are stored as questions, never decisions.
The settings page talks to `lib.people` through `mary/settings/people.py`.

## Bricks

Bricks live in `mary/skills/`, one file per ability: apps, windows, sound_screen, media, pc_status, power, web,
files, keyboard, screenshot, notes, timers, powershell, read_screen, music, messages, weather, screen_control,
memory, clock. Each has the same shape. See [skills.md](skills.md) to write one.

## Project layout

```
mary/
  __main__.py     CLI: voice mode (default), --background, --text, --hud, --debug
  core.py         orchestrator: request -> quick reply / reflex / brain (new mode), or router (today mode)
  config.py       settings: config.yaml + user/config.yaml
  brain/          the brain socket: Brain interface, ollama.py (this PC or Ollama's cloud), api.py (any OpenAI-compatible service)
  llm/            agent loop, prompts, live context, reply filter, Ollama server checks, bench
  reflexes.py     instant commands (whole-sentence matches, declared by the bricks)
  tools/          registry.py (the brick slot + safety gate), safety.py (grounding, PowerShell blocklist)
  ready.py        the feedback loop: waiting until apps and pages are really ready (every tool call)
  skills/         the bricks, one ability per file; _template.py for new ones
  router/         the semantic router (today mode)
  audio/          mic, VAD, clean-up, speech to text (local and cloud), voice (local and cloud), wake word,
                  wake replies, model downloads
  voice.py        the voice loop: trigger -> listen -> transcribe -> MARY -> speak
  hud/            the overlay (Qt + web), its output, the tray icon
  profile.py      user/profile.yaml: what you told MARY to remember (pinned in every request)
  memory/         the memory store: store.py (the API), search.py, recall.py, diary.py, files.py, consolidate.py, bench.py
  photos/         photos in the memory: library.py (the API), ingest.py (the worker), sources, read, dates, copies, places, text, faces, cluster, people, synth, bench
  persona.py      persona packs: loading, her prompt section, names, wake phrases; persona_io.py brings them in
                  and out (python -m mary.persona: new, import-whatsapp, export-card, import-card)
  settings/       the settings page (python -m mary.settings, or the tray's Settings…)
  replay.py       python -m mary.replay: real conversations through MARY, tools faked
  release_check.py  python -m mary.release_check: nothing private gets published
  wakeword_training/  record your voice, train and test a wake word
personas/         the personas that ship (mary) and a template
user/             yours, private, never published: settings, profile, memory (user/memory/), your personas
data/             downloaded models, caches, your recordings (never published)
logs/             logs and replay reports (never published)
```
