"""Logging: logs/mary.log for humans, *.jsonl files for routing/latency analysis."""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

_log_dir: Path | None = None
_jsonl_lock = threading.Lock()


def setup_logging(log_dir: Path, level: str = "INFO") -> None:
    global _log_dir
    _log_dir = Path(log_dir)
    _log_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(_log_dir / "mary.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(handler)
    # Keep chatty third-party loggers out of the file unless something is wrong.
    for noisy in ("httpx", "httpcore", "urllib3", "comtypes", "PIL"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def write_jsonl(name: str, record: dict) -> None:
    """Append one JSON record to logs/<name>.jsonl."""
    if _log_dir is None:
        return
    record = {"ts": datetime.now().isoformat(timespec="seconds"), **record}
    with _jsonl_lock, open(_log_dir / f"{name}.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
