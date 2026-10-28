from datetime import datetime

from fastapi.testclient import TestClient

from backend.main import app

client = TestClient(app)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ws_assigns_session_id_and_echoes_with_timestamp():
    with client.websocket_connect("/ws") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "session"
        session_id = hello["session_id"]
        assert len(session_id) == 32

        ws.send_json({"text": "hi"})
        reply = ws.receive_json()
        assert reply["type"] == "echo"
        assert reply["session_id"] == session_id
        assert reply["data"] == {"text": "hi"}
        datetime.fromisoformat(reply["server_ts"])  # raises if not valid ISO


def test_ws_sessions_get_different_ids():
    with client.websocket_connect("/ws") as a, client.websocket_connect("/ws") as b:
        assert a.receive_json()["session_id"] != b.receive_json()["session_id"]


def test_ws_invalid_json_returns_error_and_stays_open():
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_text("not json")
        assert ws.receive_json()["type"] == "error"
        ws.send_json({"ok": True})  # connection still usable
        assert ws.receive_json()["type"] == "echo"


def test_ws_non_object_json_returns_error():
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_text("[1, 2]")
        reply = ws.receive_json()
        assert reply["type"] == "error"
        assert "object" in reply["reason"]
