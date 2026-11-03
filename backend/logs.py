"""Structured logs: one JSON object per line, with the session id attached from a contextvar.

The id is set once per WebSocket connection (`session_id_var.set(...)`). A contextvar is per asyncio task, and
`create_task` copies the context at creation, so the receive loop, the turn tasks it spawns and any module they call
(reliability.py, the providers) all log with the right id without it being passed around. Two connections never mix ids.
"""
import contextvars
import json
import logging
import sys
from datetime import datetime, timezone

session_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("session_id", default=None)

_OURS = "_voice_coach_handler"  # marks the handler we installed, so configure_logging is idempotent and leaves others alone


class SessionContextFilter(logging.Filter):
    """Copies the current session id onto every record. Put on the HANDLER: logger-level filters skip records that
    propagate up from child loggers."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.session_id = session_id_var.get()
        return True


class JsonFormatter(logging.Formatter):
    """Fixed keys: ts, level, logger, msg, session_id. Optional: `fields` (from `extra={"fields": {...}}`, kept in its own
    object so it can never overwrite a fixed key) and `exc` (the traceback as one string, so the entry stays one line)."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "session_id": getattr(record, "session_id", None),
        }
        fields = getattr(record, "fields", None)
        if fields:
            entry["fields"] = fields
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)  # default=str: an odd value in `fields` must not break logging


def configure_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger()
    for handler in [h for h in root.handlers if getattr(h, _OURS, False)]:
        root.removeHandler(handler)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(SessionContextFilter())
    setattr(handler, _OURS, True)
    root.addHandler(handler)
    root.setLevel(level)  # uvicorn only configures its own loggers; without this our INFO lines are hidden
