# Privacy

**By default nothing leaves your PC.** The language model (Ollama), speech recognition (Whisper), the voice
(Kokoro), the wake word, memory and logs all run and stay on your computer.

## What stays where

| What | Where | Leaves the PC? |
|---|---|---|
| Your settings, profile (what MARY remembers about you) | `user/` | never |
| Your API keys | `.env` | never (only sent to the service they belong to) |
| Your wake-word recordings and trained model | `data/wakeword/`, `data/models/wakeword/` | never |
| Logs (what you said, what she did, timings) | `logs/` | never |
| Screenshots, notes | `Pictures\MARY`, `Documents\MARY Notes` | never |

WhatsApp chat names and screen text are kept out of the log file.

## Optional cloud services

You can switch these on in `user/config.yaml`. Each one sends something away, so it's off by default:

| Setting | What it sends | To |
|---|---|---|
| `stt.provider: elevenlabs` | what you say after the wake word (the wake word itself stays local) | ElevenLabs |
| `tts.provider: elevenlabs` | everything she says, including messages she reads out to you | ElevenLabs |

Both use your ElevenLabs plan's credits, and ElevenLabs keeps requests by default (turning that off is an
enterprise option). If the service can't be reached, MARY falls back to the local Whisper and Kokoro.

The web, weather and messages bricks open pages in your own browser, as you would.

## Publishing your own copy

`user/`, `data/`, `logs/` and `.env` are in `.gitignore`. Before every commit, `python -m mary.release_check`
(installed as a git pre-commit hook with `--install-hook`) scans everything that would be published for:
- keys and tokens
- your name and the names in your profile
- your voice ID and your Windows user folder
- big files
- anything in those private folders

Add other private words, one per line, to `user/private_words.txt`.
