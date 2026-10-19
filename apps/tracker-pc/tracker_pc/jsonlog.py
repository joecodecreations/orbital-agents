"""The log format of orbital/core/logs.py: one JSON object per line,
{"ts":"2026-01-02T03:04:05.678Z","level":"INFO","component":"tracker","msg":"...",...fields},
rolled by size like Python's RotatingFileHandler. Never pass secrets or request bodies as fields."""

from __future__ import annotations

import json
import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "WARN": 30, "ERROR": 40, "CRITICAL": 40}


class JSONLineLogger:
    def __init__(self, file: Path, component: str = "tracker", max_bytes: int = 5_000_000,
                 backups: int = 3, level: str = "INFO", echo: bool = False) -> None:
        self.file = file
        self.component = component
        self.max_bytes = max(max_bytes, 256)
        self.backups = max(backups, 0)
        self.minimum = LEVELS.get(level.upper(), 20)
        self.echo = echo
        self._lock = threading.Lock()

    def debug(self, msg: str, **fields: Any) -> None:
        self.log("DEBUG", msg, fields)

    def info(self, msg: str, **fields: Any) -> None:
        self.log("INFO", msg, fields)

    def warning(self, msg: str, **fields: Any) -> None:
        self.log("WARNING", msg, fields)

    def error(self, msg: str, **fields: Any) -> None:
        self.log("ERROR", msg, fields)

    def log(self, level: str, msg: str, fields: dict[str, Any]) -> None:
        if LEVELS.get(level, 20) < self.minimum:
            return
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        record: dict[str, Any] = {"ts": ts, "level": level, "component": self.component, "msg": msg}
        for k, v in fields.items():
            if v is not None and k not in record:
                record[k] = v
        line = json.dumps(record, ensure_ascii=False, default=str) + "\n"
        with self._lock:
            if self.echo:
                sys.stderr.write(line)
            try:
                self.file.parent.mkdir(parents=True, exist_ok=True)
                size = self.file.stat().st_size if self.file.exists() else 0
                if size and size + len(line.encode()) > self.max_bytes:
                    self._roll()
                with open(self.file, "a", encoding="utf-8") as fh:
                    fh.write(line)
            except OSError:
                pass

    def _roll(self) -> None:
        if self.backups == 0:
            self.file.unlink(missing_ok=True)
            return
        for i in range(self.backups, 0, -1):
            src = self.file if i == 1 else self.file.with_name(f"{self.file.name}.{i - 1}")
            dst = self.file.with_name(f"{self.file.name}.{i}")
            if src.exists():
                os.replace(src, dst)
