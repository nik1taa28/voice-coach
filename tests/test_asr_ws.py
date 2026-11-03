from fastapi.testclient import TestClient

from backend.asr import FakeASR
from backend.audio import SAMPLE_RATE
from backend.main import app

client = TestClient(app)

HALF_SECOND = b"\x00\x00" * (SAMPLE_RATE // 2)
ONE_SECOND = b"\x00\x00" * SAMPLE_RATE


def open_session(ws):
    return ws.receive_json()["session_id"]


def assert_still_usable(ws):
    """Proves the connection is open and no stray messages are queued ahead of the echo."""
    ws.send_json({"ping": 1})
    assert ws.receive_json()["type"] == "echo"


def test_full_utterance_returns_timed_fake_transcript():
    with client.websocket_connect("/ws") as ws:
        session_id = open_session(ws)
        ws.send_json({"type": "utterance_start", "sample_rate": SAMPLE_RATE})
        ws.send_bytes(HALF_SECOND)
        ws.send_bytes(HALF_SECOND)
        ws.send_json({"type": "utterance_end"})
        reply = ws.receive_json()
        assert reply["type"] == "transcript"
        assert reply["session_id"] == session_id
        assert reply["text"] == "fake transcript (1000 ms of audio)"
        assert reply["provider"] == "fake"
        assert reply["audio_ms"] == 1000
        assert reply["asr_ms"] >= 0
        assert reply["rtf"] >= 0


def test_asr_ms_reflects_provider_time(monkeypatch):
    monkeypatch.setattr("backend.main.get_asr_provider", lambda: FakeASR(delay_s=0.05))
    with client.websocket_connect("/ws") as ws:
        open_session(ws)
        ws.send_json({"type": "utterance_start", "sample_rate": SAMPLE_RATE})
        ws.send_bytes(ONE_SECOND)
        ws.send_json({"type": "utterance_end"})
        reply = ws.receive_json()
        assert reply["asr_ms"] >= 45  # 50 ms sleep, small tolerance for clock granularity
        assert reply["rtf"] < 1


def test_audio_before_start_is_error_and_connection_survives():
    with client.websocket_connect("/ws") as ws:
        open_session(ws)
        ws.send_bytes(HALF_SECOND)
        assert "before utterance_start" in ws.receive_json()["reason"]
        assert_still_usable(ws)


def test_end_without_start_is_error():
    with client.websocket_connect("/ws") as ws:
        open_session(ws)
        ws.send_json({"type": "utterance_end"})
        assert ws.receive_json()["type"] == "error"
        assert_still_usable(ws)


def test_empty_utterance_is_error():
    with client.websocket_connect("/ws") as ws:
        open_session(ws)
        ws.send_json({"type": "utterance_start", "sample_rate": SAMPLE_RATE})
        ws.send_json({"type": "utterance_end"})
        assert "no audio" in ws.receive_json()["reason"]
        assert_still_usable(ws)


def test_odd_byte_count_is_error():
    with client.websocket_connect("/ws") as ws:
        open_session(ws)
        ws.send_json({"type": "utterance_start", "sample_rate": SAMPLE_RATE})
        ws.send_bytes(b"\x00\x00\x00")
        ws.send_json({"type": "utterance_end"})
        assert "even" in ws.receive_json()["reason"]
        assert_still_usable(ws)


def test_bad_sample_rate_is_error():
    with client.websocket_connect("/ws") as ws:
        open_session(ws)
        ws.send_json({"type": "utterance_start", "sample_rate": 8000})
        assert "sample_rate" in ws.receive_json()["reason"]
        assert_still_usable(ws)


def test_second_start_discards_first_utterance():
    with client.websocket_connect("/ws") as ws:
        open_session(ws)
        ws.send_json({"type": "utterance_start", "sample_rate": SAMPLE_RATE})
        ws.send_bytes(HALF_SECOND)
        ws.send_json({"type": "utterance_start", "sample_rate": SAMPLE_RATE})
        assert "already in progress" in ws.receive_json()["reason"]
        ws.send_json({"type": "utterance_end"})  # nothing active now
        assert ws.receive_json()["type"] == "error"
        assert_still_usable(ws)


def test_overlong_utterance_reports_once_then_drops_silently():
    with client.websocket_connect("/ws") as ws:
        open_session(ws)
        ws.send_json({"type": "utterance_start", "sample_rate": SAMPLE_RATE})
        for _ in range(61):  # 61 s > 60 s cap; the 61st chunk trips it
            ws.send_bytes(ONE_SECOND)
        assert "longer than" in ws.receive_json()["reason"]
        ws.send_bytes(ONE_SECOND)  # dropped silently
        ws.send_json({"type": "utterance_end"})  # no second error, no transcript
        assert_still_usable(ws)  # next message is the echo, so nothing extra was queued


def test_utterances_can_repeat_on_one_connection():
    with client.websocket_connect("/ws") as ws:
        open_session(ws)
        for _ in range(2):
            ws.send_json({"type": "utterance_start", "sample_rate": SAMPLE_RATE})
            ws.send_bytes(ONE_SECOND)
            ws.send_json({"type": "utterance_end"})
            assert ws.receive_json()["type"] == "transcript"
