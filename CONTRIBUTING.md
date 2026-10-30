# Contributing to MARY

Thank you for helping. MARY runs on ordinary laptops with no GPU, so every change is judged by two things:
does she behave better, and does she stay fast on a CPU.

## Getting set up
1. Windows 10/11, Python 3.12. Run `setup.bat` (it installs everything, including Ollama and the models).
2. `run.bat --text` to type to her, or `run.bat` for voice.
3. Your own settings go in `user/config.yaml` (private). Don't edit `config.yaml` for yourself: it holds the
   defaults everyone gets.

## Before you open a pull request
- `.venv\Scripts\python.exe -m pytest`: all tests pass. Tests run on the shipped defaults, never on your
  `user/` folder.
- `.venv\Scripts\python.exe -m mary.release_check`: nothing private (install it as a hook once with
  `--install-hook`).
- If you changed how she decides or talks, run `python -m mary.replay` before and after, and say what changed
  in the PR.
- If you changed something slow (prompts, models, audio), measure it: `--debug` prints per-stage latency, and
  `logs/latency.jsonl` keeps it.

## Style
- Code reads like the code around it: short functions, comments that say *why*, measurements in comments when
  a choice was made by measuring.
- User-facing text is plain English, said the way a person would say it.
- New abilities are bricks (see [docs/skills.md](docs/skills.md)); the core changes rarely.
- Never delete the user's files permanently, never act without words of the right kind, and never send
  anything to a cloud service unless the user switched it on.
