# Personas: who the assistant is

The assistant's character lives in a **persona pack**: one folder that says who they are, how they talk and write,
what they sound like, what wakes them, and the stories they know. MARY ships with one (`personas/mary`). You can
make your own (a grandmother, a coach, a butler, a character), keep it private, and share it with others.

## Make one

The easy way is the settings page (tray icon › **Settings…**, or `settings.bat`): open the persona switcher at the
top left › **New persona**.
- Answer a few questions: name, who they are to you, nicknames, a starting personality, things they often say, a
  story. Or start blank, or duplicate an existing persona.
- Then shape it on the **Memory** pages (Identity, Personality, Stories) and on **Voice** and **Wake
  word**, and press **Make … active** at the top of the page.

Or from the command line:
```powershell
.venv\Scripts\python.exe -m mary.persona new gran
```
That copies `personas/_template` to `user/personas/gran/` (private: `user/` is never published). Fill in its
files, then pick it in `user/config.yaml`:
```yaml
persona: gran
```
Restart MARY. `python -m mary.persona list` shows the personas you have.

## What's in the folder

| File | What it does |
|---|---|
| `persona.yaml` | name, nicknames, wake phrases, wake responses, greeting, voice |
| `soul.md` | the personality, in a few sentences written to the assistant ("You are…"): warmth, humour, values, how they treat you |
| `speech.md` | how they talk out loud, with a few example exchanges |
| `texts/*.txt` | real messages they wrote (separated by blank lines), shown as examples whenever MARY writes; how they write is described in `soul.md` |
| `lore/*.md` | stories, sayings and family facts, one per file; the first line is `keywords: a, b, c` (files starting with `_` are examples, never used) |

`soul.md` and `speech.md` become part of the instructions, which stay the same while MARY runs, so they're read
once and cached. Lore and text examples are added to a single request only when they fit: a lore entry when one of
its keywords is said, and a couple of example messages when the assistant is about to write something. The rest of
the time they cost nothing.

## Writing tips (what works with a small model)

- **Keep it short.** A few clear sentences beat a page. The settings page shows when a description gets long.
- **Say what they do, not what they don't.** A small model copies the words it reads: "never call him Mom" made
  it say "Mom". Write "You call him by his name, now and then" instead.
- **Examples beat descriptions,** and they keep the character from drifting in long conversations. Put a few real
  exchanges in `speech.md`.
- **No facts in examples.** An example reply mentioning the weather was once repeated as the real weather.
- **Nicknames:** list what you call the assistant under `nicknames` (older files say `user_calls_her`, which still
  works). If one is ever used as *your* name ("Got it, Mom."), it's dropped before it's spoken.

## The voice

On the settings page: **Voice**. Pick from the voices on this PC (Kokoro: female and male, American and British,
free and private) or your ElevenLabs voices, press play on any of them, and set the speed. In `persona.yaml` it's
`voice:`, with the same settings as `tts:` in `config.yaml`:
```yaml
voice:
  provider: kokoro          # free, on your PC
  voice: am_michael
```
or a voice from your ElevenLabs account (one you made, for example; see [privacy](privacy.md)):
```yaml
voice:
  provider: elevenlabs
  voice: af_heart           # Kokoro, used when ElevenLabs can't be reached
  elevenlabs: {voice_id: "…", model: eleven_v4_turbo}
```

## The wake phrase

On the settings page: **Wake word**.
- **Ready-made phrases work straight away:** Hey Jarvis, Alexa, Hey Mycroft, Hey Rhasspy (openWakeWord's own).
- **Your own phrase** ("Hey Grandma") is trained on your voice, in the browser:
  1. About 5 minutes of recording: around 32 takes of the phrase said in different ways, a few sound-alikes that
     must not wake it, and (once, shared by every phrase) 25 seconds of reading aloud and 20 seconds of your room.
  2. 10–20 minutes of training on this PC, at low priority, with a progress bar. It carries on if you close the
     page or restart MARY.

  The first training downloads openWakeWord's 185 MB of background sounds. Until a phrase passes, use the hotkey.

In `persona.yaml`:
```yaml
wake:
  - phrase: Hey Gran
    model: hey_gran            # data/models/wakeword/hey_gran.onnx, trained on your voice (or hey_jarvis, alexa…)
    names: [gran, granny]      # how speech recognition writes the name
    alone: [gran]              # said on its own, this counts too
    bare: false                # true if "Gran, open Notepad" should count as the wake phrase
```
From the command line: `python -m mary.wakeword_training.record --phrase "Hey Gran"`, then
`python -m mary.wakeword_training.train --phrase "Hey Gran" --enable` (see
[the guide](guide.md#wake-word-hey-mary-or-hey-mom)).

## Bring them in from somewhere else

- **Real messages:** Identity › **Import messages** takes any `.txt` file. Blank lines separate the
  messages. If it's a chat export (in WhatsApp: the chat › More › Export chat › without media), it asks whose
  messages to keep. They stay on your PC, in the persona's `texts/` folder. From the command line:
  ```powershell
  .venv\Scripts\python.exe -m mary.persona import-whatsapp "WhatsApp Chat with Gran.txt" --sender "Gran" --into gran
  ```
- **Character Cards** (V2/V3, `.json` or `.png`), the format character apps use to share characters: the persona
  switcher › **Import a character card**, and the switcher › **Export as a character card**. From the command line:
  ```powershell
  .venv\Scripts\python.exe -m mary.persona import-card "Gran.png" --as gran
  ```
  ```powershell
  .venv\Scripts\python.exe -m mary.persona export-card gran --out gran.card.json
  ```
  Export leaves your ElevenLabs voice ID out unless you ask for it (`--with-voice`): a voice made from someone's
  recordings is yours to share or not.
