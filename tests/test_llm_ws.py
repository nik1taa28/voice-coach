import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.audio import SAMPLE_RATE
from backend.llm import FakeLLM, LLMError, Message
from backend.main import app

client = TestClient(app)
ONE_SECOND = b"\x00\x00" * SAMPLE_RATE


class RecordingLLM(FakeLLM):
    """FakeLLM that remembers every prompt it was given, and can fail on chosen call numbers."""

    def __init__(self, fail_on: tuple[int, ...] = ()) -> None:
        super().__init__()
        self.calls: list[list[Message]] = []
        self.fail_on = fail_on

    async def stream(self, messages):
        self.calls.append(messages)
        if len(self.calls) in self.fail_on:
            raise LLMError("injected failure")
        async for piece in super().stream(messages):
            yield piece


@pytest.fixture
def llm(monkeypatch):
    provider = RecordingLLM()
    monkeypatch.setattr("backend.main.get_llm_provider", lambda: provider)
    return provider


def read_until(ws, kind, **fields):
    """Collect messages up to and including the first one matching type (and fields)."""
    seen = []
    while True:
        msg = ws.receive_json()
        seen.append(msg)
        if msg["type"] == kind and all(msg.get(k) == v for k, v in fields.items()):
            return seen


def start_interview(ws):
    ws.receive_json()  # session
    ws.send_json({"type": "start", "topic": "Python", "difficulty": "easy"})
    return read_until(ws, "state", state="listening")


def speak(ws):
    ws.send_json({"type": "utterance_start", "sample_rate": SAMPLE_RATE})
    ws.send_bytes(ONE_SECOND)
    ws.send_json({"type": "utterance_end"})


def types(messages):
    return [m["type"] for m in messages]


def test_start_streams_opening_question(llm):
    with client.websocket_connect("/ws") as ws:
        msgs = start_interview(ws)
        assert types(msgs)[0] == "state" and msgs[0]["state"] == "thinking"
        deltas = [m["text"] for m in msgs if m["type"] == "coach_delta"]
        done = next(m for m in msgs if m["type"] == "coach_done")
        assert len(deltas) > 1  # really arrived in pieces
        assert "".join(deltas).strip() == done["text"]
        assert done["provider"] == "fake"
        assert 0 <= done["llm_ttft_ms"] <= done["llm_total_ms"]


def test_answer_gets_follow_up_and_history_reaches_the_llm(llm):
    with client.websocket_connect("/ws") as ws:
        start_interview(ws)
        speak(ws)
        msgs = read_until(ws, "state", state="listening")
        assert types(msgs)[:2] == ["transcript", "state"]
        done = next(m for m in msgs if m["type"] == "coach_done")
        assert done["text"].startswith("Fake question 2")
    second_prompt = llm.calls[1]
    assert [m.role for m in second_prompt] == ["system", "user", "assistant", "user"]
    assert second_prompt[-1].text == "fake transcript (1000 ms of audio)"


def test_pause_blocks_recording_and_resume_restores_it(llm):
    with client.websocket_connect("/ws") as ws:
        start_interview(ws)
        ws.send_json({"type": "pause"})
        assert ws.receive_json()["state"] == "paused"
        ws.send_json({"type": "utterance_start", "sample_rate": SAMPLE_RATE})
        assert "cannot record while paused" in ws.receive_json()["reason"]
        ws.send_json({"type": "resume"})
        assert ws.receive_json()["state"] == "listening"
        speak(ws)
        assert read_until(ws, "state", state="listening")


def test_end_sends_summary_and_locks_the_session(llm):
    with client.websocket_connect("/ws") as ws:
        start_interview(ws)
        speak(ws)
        read_until(ws, "state", state="listening")
        ws.send_json({"type": "end"})
        msgs = read_until(ws, "summary")
        assert msgs[0]["state"] == "ended"
        assert msgs[-1]["text"].startswith("Fake summary")
        assert msgs[-1]["provider"] == "fake"
        ws.send_json({"type": "pause"})
        assert "cannot pause while ended" in ws.receive_json()["reason"]


def test_end_without_answers_skips_the_llm(llm):
    with client.websocket_connect("/ws") as ws:
        start_interview(ws)
        ws.send_json({"type": "end"})
        summary = read_until(ws, "summary")[-1]
        assert summary["provider"] == "none"
    assert len(llm.calls) == 1  # only the opening question


def test_invalid_start_is_rejected_and_can_be_retried(llm):
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "start", "topic": "Python", "difficulty": "insane"})
        assert "difficulty" in ws.receive_json()["reason"]
        ws.send_json({"type": "start", "topic": "Python", "difficulty": "easy"})
        assert read_until(ws, "state", state="listening")


def test_opener_failure_returns_to_idle_and_can_retry(monkeypatch):
    provider = RecordingLLM(fail_on=(1, 2))  # M4 retries once before the first piece, so the failure must outlast the retry
    monkeypatch.setattr("backend.main.get_llm_provider", lambda: provider)
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "start", "topic": "Python", "difficulty": "easy"})
        msgs = read_until(ws, "state", state="idle")
        assert "coach reply failed" in [m.get("reason") for m in msgs]
        ws.send_json({"type": "start", "topic": "Python", "difficulty": "easy"})
        assert read_until(ws, "coach_done")


def test_mid_conversation_failure_rolls_back_and_retry_works(monkeypatch):
    provider = RecordingLLM(fail_on=(2, 3))  # the answer's first try and its one retry both fail (M4 retry)
    monkeypatch.setattr("backend.main.get_llm_provider", lambda: provider)
    with client.websocket_connect("/ws") as ws:
        start_interview(ws)
        speak(ws)
        msgs = read_until(ws, "state", state="listening")  # the rollback state
        assert "coach reply failed" in [m.get("reason") for m in msgs]
        speak(ws)  # user answers again
        done = next(m for m in read_until(ws, "state", state="listening") if m["type"] == "coach_done")
        assert done["text"].startswith("Fake question 2")  # the failed answer was not kept
    assert [m.role for m in provider.calls[3]] == ["system", "user", "assistant", "user"]  # call 4: the second answer


def test_llm_unavailable_at_connect_reports_and_closes(monkeypatch):
    def broken():
        raise LLMError("GEMINI_API_KEY is not set")

    monkeypatch.setattr("backend.main.get_llm_provider", broken)
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "session"
        assert "GEMINI_API_KEY" in ws.receive_json()["reason"]
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
