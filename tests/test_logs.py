"""M5: JSON log lines, and the session id reaching them through a contextvar."""
import asyncio
import json
import logging

from fastapi.testclient import TestClient

from backend.logs import JsonFormatter, SessionContextFilter, configure_logging, session_id_var
from backend.main import app

client = TestClient(app)


class Collect(logging.Handler):
    """Formats like production (filter + formatter) and keeps the lines."""

    def __init__(self) -> None:
        super().__init__()
        self.addFilter(SessionContextFilter())
        self.setFormatter(JsonFormatter())
        self.lines: list[dict] = []

    def emit(self, record: logging.LogRecord) -> None:
        line = self.format(record)
        assert "\n" not in line  # one entry = one line, always
        self.lines.append(json.loads(line))


def make_logger(name: str) -> tuple[logging.Logger, Collect]:
    handler = Collect()
    log = logging.getLogger(name)
    log.handlers[:] = [handler]
    log.propagate = False
    log.setLevel(logging.DEBUG)
    return log, handler


def test_entry_has_fixed_keys_and_fields_stay_separate():
    log, h = make_logger("t.fixed")
    log.info("hello", extra={"fields": {"msg": "tries to overwrite", "ms": 12}})
    entry = h.lines[0]
    assert set(entry) == {"ts", "level", "logger", "msg", "session_id", "fields"}
    assert entry["msg"] == "hello"  # the extra "msg" lives inside fields, it cannot overwrite the real one
    assert entry["fields"] == {"msg": "tries to overwrite", "ms": 12}
    assert entry["level"] == "INFO" and entry["logger"] == "t.fixed"


def test_quotes_and_newlines_in_the_message_stay_valid_json():
    log, h = make_logger("t.escape")
    log.info('she said "hi"\nsecond line\t%s', "and {braces}")
    assert h.lines[0]["msg"] == 'she said "hi"\nsecond line\tand {braces}'


def test_exception_goes_into_exc_as_a_single_entry():
    log, h = make_logger("t.exc")
    try:
        raise ValueError("boom")
    except ValueError:
        log.exception("it failed")
    entry = h.lines[0]
    assert entry["level"] == "ERROR"
    assert "Traceback" in entry["exc"] and "ValueError: boom" in entry["exc"]


def test_unserialisable_field_does_not_break_logging():
    log, h = make_logger("t.odd")
    log.info("x", extra={"fields": {"obj": object()}})
    assert h.lines[0]["fields"]["obj"].startswith("<object")


def test_session_id_is_none_without_context_and_set_with_it():
    log, h = make_logger("t.ctx")
    log.info("before")
    token = session_id_var.set("abc")
    try:
        log.info("after")
    finally:
        session_id_var.reset(token)
    assert h.lines[0]["session_id"] is None
    assert h.lines[1]["session_id"] == "abc"


def test_tasks_keep_their_own_session_id_and_children_inherit_it():
    log, h = make_logger("t.tasks")

    async def session(name: str) -> None:
        session_id_var.set(name)
        log.info("start %s", name)
        await asyncio.sleep(0.01)  # let the other task run in between
        child = asyncio.create_task(asyncio.sleep(0))  # context is copied at creation
        await child
        log.info("end %s", name)

        async def spawned() -> None:
            log.info("from child %s", name)

        await asyncio.create_task(spawned())

    async def main() -> None:
        await asyncio.gather(session("A"), session("B"))

    asyncio.run(main())
    for entry in h.lines:
        assert entry["msg"].endswith(" " + entry["session_id"]), entry  # message names A/B, the field must agree
    assert {e["session_id"] for e in h.lines} == {"A", "B"}
    assert len(h.lines) == 6


def test_configure_logging_is_idempotent():
    configure_logging()
    configure_logging()
    ours = [h for h in logging.getLogger().handlers if getattr(h, "_voice_coach_handler", False)]
    assert len(ours) == 1
    assert logging.getLogger().level == logging.INFO


def test_two_connections_never_mix_session_ids():
    handler = Collect()
    log = logging.getLogger("voice_coach")
    log.addHandler(handler)
    try:
        with client.websocket_connect("/ws") as a, client.websocket_connect("/ws") as b:
            ids = []
            for ws in (a, b):
                ids.append(ws.receive_json()["session_id"])
                ws.send_json({"type": "start", "topic": "Python", "difficulty": "easy"})
            for ws in (a, b):
                while ws.receive_json()["type"] != "coach_done":
                    pass
    finally:
        log.removeHandler(handler)
    assert ids[0] != ids[1]
    done = [e["session_id"] for e in handler.lines if e["msg"] == "coach turn done"]
    assert sorted(done) == sorted(ids)  # one turn each, each labelled with its own connection's id
    connected = [e for e in handler.lines if e["msg"] == "connected"]
    assert {e["session_id"] for e in connected} >= set(ids)
