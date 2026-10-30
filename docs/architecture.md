# How MARY is built

MARY is built like Lego: a **core** that every ability relies on, and **skill bricks**, one ability each, that
you can add or remove without touching the core.

## The core

| Part | Where | Job |
|---|---|---|
| Ears | `mary/audio/` | the wake word or Ctrl+Space, the microphone and its clean-up (`clean.py`), speech to text: local Whisper (`stt.py`) or ElevenLabs Scribe, live (`live_stt.py`, `cloud_stt.py`) |
| Voice | `mary/audio/tts.py`, `cloud_tts.py` | speaking sentence by sentence while she's still thinking, numbers and dates said naturally; Kokoro locally or an ElevenLabs voice |
| Brain socket | `mary/brain/` | the one place a language model plugs in (`Brain`; `ollama.py` today) |
| Agent loop | `mary/llm/agent.py` | the model, native tool calling, optional thinking, the cache-friendly prompt layout |
| Memory | `mary/profile.py`, the agent's history | you (`user/profile.yaml`), the conversation so far |
| Safety gate | `mary/tools/safety.py`, `registry.py` | checks every action against your words; asks only for risky steps |
| Brick slot | `mary/tools/registry.py`, `mary/skills/__init__.py` | loads the bricks named under `skills:` |
| Reflexes | `mary/reflexes.py` | the few instant commands: volume, brightness, pause/skip, lock, the time |
| Settings | `mary/config.py` | `config.yaml` (defaults) with `user/config.yaml` (yours) merged over it |

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
[system prompt + tool schemas]        static: identical on every call (cached)
[what you asked MARY to remember]     a pinned first exchange; changes rarely
[the conversation so far]             append-only, trimmed only every few turns
[<context> time, front window… + your request]   the only new part each turn
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
  brain/          the brain socket: Brain interface, ollama.py (the local model)
  llm/            agent loop, prompts, live context, reply filter, Ollama server checks, bench
  reflexes.py     instant commands (whole-sentence matches, declared by the bricks)
  tools/          registry.py (the brick slot + safety gate), safety.py (grounding, PowerShell blocklist)
  skills/         the bricks, one ability per file; _template.py for new ones
  router/         the semantic router (today mode)
  audio/          mic, VAD, clean-up, speech to text (local and cloud), voice (local and cloud), wake word,
                  wake replies, model downloads
  voice.py        the voice loop: trigger -> listen -> transcribe -> MARY -> speak
  hud/            the overlay (Qt + web), its output, the tray icon
  profile.py      user/profile.yaml: what MARY remembers about you
  replay.py       python -m mary.replay: real conversations through MARY, tools faked
  release_check.py  python -m mary.release_check: nothing private gets published
  wakeword_training/  record your voice, train and test a wake word
user/             yours, private, never published: settings, profile (and later personas, memory)
data/             downloaded models, caches, your recordings (never published)
logs/             logs and replay reports (never published)
```
