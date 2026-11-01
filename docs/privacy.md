# Privacy

**By default nothing leaves your PC.** The language model (Ollama), speech recognition (Whisper), the voice
(Kokoro), the wake word, memory and logs all run and stay on your computer, unless you choose a cloud model or
service below.

## What stays where

| What | Where | Leaves the PC? |
|---|---|---|
| Your settings, profile (what MARY remembers about you) | `user/` | never |
| The diary of what you asked, the facts she learned, day summaries (`memory.db`, `facts.md`, `diary/`) | `user/memory/` | never (see the cloud model row below) |
| Your photos' records, thumbnails and the words read in them (only for folders you add) | `user/memory/` | never; logs hold counts and timings only |
| **Face fingerprints, face pictures and the names you gave people** (unless `photos.faces.allowed` is false) | `user/memory/` | never; deleted with the person, copies included; logs hold counts only |
| The two face models (YuNet, SFace, about 39 MB) | `data/models/faces/` | they come *to* your PC, once, when you ask; nothing of yours goes out |
| The list of place names (GeoNames, CC BY 4.0) | `data/places/` | it comes *to* your PC, once, when you ask; nothing of yours goes out |
| Your API keys | `.env` | never (only sent to the service they belong to) |
| Your wake-word recordings and trained model | `data/wakeword/`, `data/models/wakeword/` | never |
| Logs (what you said, what she did, timings) | `logs/` | never |
| How long your apps take to be ready (the app's name and the seconds, never a window title) | `logs/ready.jsonl` | never |
| Screenshots, notes | `Pictures\MARY`, `Documents\MARY Notes` | never |

WhatsApp chat names and screen text are kept out of the log file. To tell when an app has finished loading, MARY compares a tiny
picture of its window (24 × 16 grey dots) from moment to moment; it's only held in memory, never saved.

## Optional cloud services

You can switch these on in `user/config.yaml`. Each one sends something away, so it's off by default:

| Setting | What it sends | To |
|---|---|---|
| `stt.provider: elevenlabs` | what you say after the wake word (the wake word itself stays local) | ElevenLabs |
| `tts.provider: elevenlabs` | everything she says, including messages she reads out to you | ElevenLabs |
| a cloud model (`llm.model: beam:cloud`, anything ending in `cloud`) | each request to the model: her instructions, what she remembers about you, any lines from her memory that fit the request, the conversation, and what tools found to answer it (screen text, file names, chat names, the weather) | ollama.com |
| `memory.consolidate.enabled: true` with a cloud model or service | in the background, while you're away: what you said in recent diary lines (not her replies, and nothing from a skill that read messages or the screen) | the same service. Off by default; with a model on this PC nothing leaves |
| `brain.provider: api` | the same, plus your API key | the service at `brain.api.url` |

The settings page asks before switching to either, and its Test button sends only "Say hello in five words."
MARY never sends the model anything between requests for these, as she does to keep a model on this PC ready.

Both use your ElevenLabs plan's credits, and ElevenLabs keeps requests by default (turning that off is an
enterprise option). If the service can't be reached, MARY falls back to the local Whisper and Kokoro.

Web pages open in **MARY's own browser** window (your Edge, with its own profile in `data/browser/`, never
published). What you sign into there (WhatsApp Web, YouTube) stays in that profile; sites see an ordinary Edge
visit. With `web.own_browser: false` they open in your default browser instead.

The weather is a plain request to wttr.in with the place (from `weather.place`, or the site guesses from your
connection), the same thing a browser would send.

**Pictures of your screen** go to the vision model only when MARY has to *see* something (`look`). By default
that's a model on this PC, so they stay here. If you set `vision.model` to a cloud model, the picture of that
window or area is sent to ollama.com.

The diary never records what a skill read out (messages, screen text, a document): a line holds your words, the skills
she used and what she said, and when a skill that reads private things was used, not even what she said. "Delete it
for good" removes a memory from the store, its search index and the text files (the last few copies MARY keeps of
`memory.db` before an upgrade are the one place an old copy could remain, in `user/backups/`).

**Photos** are read only from folders you add, on this PC, by code (and Windows' own OCR); originals are never changed, and "remove" deletes
MARY's record, thumbnail and the words from the search index for good. Photos are not sent to a cloud model in this phase: nothing
here has a model look at a picture. The one network request photos make is the optional fetch of the list of places (11.3 MB from
download.geonames.org), which sends nothing of yours.

**Faces** are biometric. Recognising them is **on by default** (it only starts once you fetch the two models on Settings › People), and `photos.faces.allowed:
false` turns it off completely. A face is found and fingerprinted by two small models running on this PC; fingerprints and
the small face pictures stay here, are never sent to any model or cloud and never written to a log. MARY never makes up a name: a name
is only ever one you typed. **Deleting a person deletes their fingerprints, pictures and links for good**, from the database and from
the copies MARY keeps before a format change; **Delete all face data** deletes everything and switches it off. The settings page shows
a face only to itself, and tells the browser not to keep it. The one network request is the optional 39 MB fetch of the models from
huggingface.co/opencv, which sends nothing of yours. See [people.md](people.md), including a caveat on the models' training data for
anything beyond your own PC.

## Publishing your own copy

`user/`, `data/`, `logs/` and `.env` are in `.gitignore`. Before every commit, `python -m mary.release_check`
(installed as a git pre-commit hook with `--install-hook`) scans everything that would be published for:
- keys and tokens
- your name and the names in your profile
- your voice ID and your Windows user folder
- big files
- anything in those private folders

Add other private words, one per line, to `user/private_words.txt`.
