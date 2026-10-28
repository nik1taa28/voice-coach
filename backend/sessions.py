"""Pure helpers for session handling. No FastAPI imports, so they are trivial to unit test."""
import uuid
from datetime import datetime, timezone


def new_session_id() -> str:
    return uuid.uuid4().hex


def server_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_echo(session_id: str, message: dict) -> dict:
    return {
        "type": "echo",
        "session_id": session_id,
        "data": message,
        "server_ts": server_timestamp(),
    }


def build_error(session_id: str, reason: str) -> dict:
    return {"type": "error", "session_id": session_id, "reason": reason}
