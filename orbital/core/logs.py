"""Central logging: ~/.orbital-agents/logs/<component>.log, one JSON object per line, rolled by size.

    from orbital.core import get_logger
    log = get_logger("hub")
    log.info("section saved", extra={"fields": {"section": "home-server", "blocks": 4}})

Each line: {"ts": "...Z", "level": "INFO", "component": "hub", "msg": "...", ...fields}.
Size and count come from [logging] in config.toml. `orbital logs <component> [-f]` reads them,
and the watchdog deletes rolled files older than logging.retention_days.
Components: hub, satellite, scheduler, watchdog, browser-bridge, tracker, app (Mac), wizard, cli, feeds.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import sys
import time
from typing import Any

from orbital.core.config import load_config

_configured: dict[str, logging.Logger] = {}


class JsonLineFormatter(logging.Formatter):
    def __init__(self, component: str):
        super().__init__()
        self.component = component

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "component": self.component,
            "msg": record.getMessage(),
        }
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            entry.update(fields)
        if record.exc_info:
            entry["error"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False, default=str)


def get_logger(component: str, home: str | None = None, echo: bool | None = None) -> logging.Logger:
    """Logger for one component. Safe to call repeatedly. echo=True also prints to stderr."""
    if component in _configured:
        return _configured[component]
    cfg = load_config(home)
    cfg.paths.ensure()
    logger = logging.getLogger(f"orbital.{component}")
    logger.setLevel(str(cfg.get("logging.level", "INFO")).upper())
    logger.propagate = False
    handler = logging.handlers.RotatingFileHandler(
        cfg.paths.log_file(component),
        maxBytes=int(cfg.get("logging.max_bytes", 5_000_000)),
        backupCount=int(cfg.get("logging.backups", 3)),
        encoding="utf-8",
    )
    handler.setFormatter(JsonLineFormatter(component))
    logger.addHandler(handler)
    if echo if echo is not None else sys.stderr.isatty():
        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(logging.Formatter(f"{component}: %(levelname)s %(message)s"))
        logger.addHandler(console)
    _configured[component] = logger
    return logger


def reset_for_tests() -> None:
    for logger in _configured.values():
        for h in list(logger.handlers):
            h.close()
            logger.removeHandler(h)
    _configured.clear()
