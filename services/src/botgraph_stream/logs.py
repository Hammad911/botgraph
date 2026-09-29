"""Structured logging for the services.

    BOTGRAPH_LOG_FORMAT=json   one JSON object per line (containers, Loki, any log shipper)
    BOTGRAPH_LOG_FORMAT=text   human-readable (default on a terminal)
    BOTGRAPH_LOG_LEVEL=INFO

Pass context as ``extra``: ``log.info("alert raised", extra={"sensor": s, "ip": ip})``; the
JSON formatter emits every extra field at the top level.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import UTC, datetime
from typing import Any

# Attributes every LogRecord has; anything else on a record came from ``extra``.
_STANDARD = set(vars(logging.makeLogRecord({}))) | {"message", "asctime", "taskName"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in vars(record).items():
            if key not in _STANDARD and not key.startswith("_"):
                entry[key] = value
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def configure_logging(fmt: str | None = None, level: str | None = None) -> None:
    """Configure the root logger once per process (idempotent)."""
    fmt = (
        fmt or os.environ.get("BOTGRAPH_LOG_FORMAT") or ("text" if sys.stderr.isatty() else "json")
    )
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        JsonFormatter()
        if fmt == "json"
        else logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel((level or os.environ.get("BOTGRAPH_LOG_LEVEL") or "INFO").upper())
    # Library chatter that is not actionable at INFO.
    for noisy in ("alembic", "httpx"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
