"""Before anything is published: python -m mary.release_check [--staged] [--install-hook]

Scans the files git would publish (tracked, plus new ones not ignored) for what must never leave this PC:
  - keys and tokens (an ElevenLabs key, "sk-…", GitHub and AWS tokens, api_key = "…" with a value)
  - your own words: your name and the names in user/profile.yaml, the voice ID in your settings, anything in
    user/private_words.txt (one per line), and your Windows user name (C:\\Users\\<you>)
  - your private folders and data: user/, data/, logs/, .env, recordings, models
  - big files (over 2 MB)
A line can be let through on purpose by putting "release-check: ignore" on it.

--staged checks only what's about to be committed; --install-hook makes git run that before every commit.
Exit code 1 when anything is found. Matches are reported by file and line; secret values are never printed.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

from mary.config import PROJECT_DIR, user_dir

MAX_BYTES = 2 * 1024 * 1024
IGNORE_MARK = "release-check: ignore"
_PRIVATE_PATHS = re.compile(r"^(user|data|logs|\.venv)/|^\.env$|^profile\.yaml$|\.(onnx|wav|npy|npz|pcm|gguf)$", re.I)
_SECRETS = [
    ("an ElevenLabs-style key", re.compile(r"\bsk_[A-Za-z0-9]{24,}\b")),
    ("an API key", re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_\-]{20,}")),
    ("a GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    ("an AWS key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("a key with its value", re.compile(r"(?i)\b(api[_-]?key|secret|token|password)\b\s*[:=]\s*['\"][^'\"\s]{12,}['\"]")),
    ("a key with its value", re.compile(r"(?i)\b[A-Z_]*API_KEY\s*=\s*[A-Za-z0-9_\-]{12,}")),
]


@dataclass
class Problem:
    path: str
    line: int
    what: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.what}" if self.line else f"{self.path}: {self.what}"


def private_words() -> list[str]:
    """Your name and the names you've told MARY, the voice ID in your settings, and user/private_words.txt."""
    words: list[str] = []
    folder = user_dir()
    try:
        profile = yaml.safe_load((folder / "profile.yaml").read_text(encoding="utf-8")) or {}
        words.append(str(profile.get("name") or ""))
        for fact in profile.get("facts") or []:  # "my sister's name is Ama" -> Ama
            words += re.findall(r"\bname(?:'s| is)\s+([A-Z][\w'-]+)", str(fact))
    except (OSError, yaml.YAMLError):
        pass
    try:
        mine = yaml.safe_load((folder / "config.yaml").read_text(encoding="utf-8")) or {}
        words.append(str(((mine.get("tts") or {}).get("elevenlabs") or {}).get("voice_id") or ""))
    except (OSError, yaml.YAMLError, AttributeError):
        pass
    try:
        words += (folder / "private_words.txt").read_text(encoding="utf-8").splitlines()
    except OSError:
        pass
    return sorted({w.strip() for w in words if len(w.strip()) >= 3 and not w.strip().startswith("#")})


def secret_values() -> list[str]:
    """Key values set in this environment (from .env): checked for, never printed."""
    try:
        from dotenv import load_dotenv

        load_dotenv(PROJECT_DIR / ".env")
    except ImportError:
        pass
    return [v for k, v in os.environ.items() if k.endswith(("_API_KEY", "_TOKEN")) and len(v) >= 12]


def check_text(path: str, text: str, words: list[str], secrets: list[str], username: str = "") -> list[Problem]:
    found = []
    word_res = [(w, re.compile(rf"(?<![\w]){re.escape(w)}(?![\w])")) for w in words]
    user_re = re.compile(rf"(?i)[\\/]Users[\\/]+{re.escape(username)}\b") if username else None
    for i, line in enumerate(text.splitlines(), 1):
        if IGNORE_MARK in line:
            continue
        if any(s in line for s in secrets):
            found.append(Problem(path, i, "contains a key from your .env"))
        for what, rx in _SECRETS:
            if rx.search(line):
                found.append(Problem(path, i, f"looks like {what}"))
                break
        for word, rx in word_res:
            if rx.search(line):
                found.append(Problem(path, i, f"mentions {word!r} (one of your private words)"))
        if user_re and user_re.search(line):
            found.append(Problem(path, i, "contains your Windows user folder"))
    return found


def check_files(paths: list[str], root: Path = PROJECT_DIR, words: list[str] | None = None,
                secrets: list[str] | None = None, username: str | None = None) -> list[Problem]:
    words = private_words() if words is None else words
    secrets = secret_values() if secrets is None else secrets
    username = os.environ.get("USERNAME", "") if username is None else username
    found: list[Problem] = []
    for rel in paths:
        rel = rel.replace("\\", "/")
        if _PRIVATE_PATHS.search(rel):
            found.append(Problem(rel, 0, "is private (your folder, data, logs, keys, recordings or models)"))
            continue
        file = root / rel
        if not file.is_file():
            continue
        if file.stat().st_size > MAX_BYTES:
            found.append(Problem(rel, 0, f"is {file.stat().st_size / 2**20:.1f} MB (over 2 MB)"))
            continue
        try:
            text = file.read_text(encoding="utf-8")
        except UnicodeDecodeError:  # images and other binaries: only their size and place are checked
            continue
        found += check_text(rel, text, words, secrets, username)
    return found


def published_files(root: Path = PROJECT_DIR, staged: bool = False) -> list[str]:
    git = ["git", "-C", str(root)]
    if staged:
        cmd = git + ["diff", "--cached", "--name-only", "--diff-filter=ACMR"]
    else:
        cmd = git + ["ls-files", "--cached", "--others", "--exclude-standard"]
    out = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if out.returncode != 0:
        raise SystemExit("Not a git repository yet: run `git init` in MARY's folder first.")
    return [line for line in out.stdout.splitlines() if line]


HOOK = """#!/bin/sh
# Installed by `python -m mary.release_check --install-hook`: nothing private gets committed.
if [ -x .venv/Scripts/python.exe ]; then py=.venv/Scripts/python.exe; else py=python; fi
exec "$py" -m mary.release_check --staged
"""


def install_hook(root: Path = PROJECT_DIR) -> Path:
    hook = root / ".git" / "hooks" / "pre-commit"
    if not hook.parent.exists():
        raise SystemExit("Not a git repository yet: run `git init` in MARY's folder first.")
    hook.write_text(HOOK, encoding="utf-8", newline="\n")
    return hook


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m mary.release_check")
    parser.add_argument("--staged", action="store_true", help="check only what's about to be committed")
    parser.add_argument("--install-hook", action="store_true", help="run this check before every git commit")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if args.install_hook:
        print(f"Installed: {install_hook()}")
        return
    files = published_files(staged=args.staged)
    problems = check_files(files)
    if not problems:
        print(f"Release check: {len(files)} files, nothing private found.")
        return
    print(f"Release check: {len(problems)} problem(s) in {len({p.path for p in problems})} file(s):")
    for p in problems:
        print(f"  {p}")
    print(f'Fix them, or put "{IGNORE_MARK}" on a line that is meant to be public.')
    sys.exit(1)


if __name__ == "__main__":
    main()
