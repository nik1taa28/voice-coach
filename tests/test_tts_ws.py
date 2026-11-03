import base64
import io
import random
import wave

import pytest
from fastapi.testclient import TestClient

from backend.audio import SAMPLE_RATE
from backend.llm import FakeLLM, LLMError
from backend.main import app
from backend.questions import Question
from backend.tts import BrowserTTS, FakeTTS

client = TestClient(app)
ONE_SECOND = b"\x00\x00" * SAMPLE_RATE
REPLY = ["One. ", "Two. ", "Three."]  # three sentences in three pieces; the last has no trailing space


class ScriptedLLM(FakeLLM):
    """Streams exactly `pieces`; optionally raises after `fail_after` pieces. Remembers every prompt."""

    def __init__(self, pieces: list[str], fail_after: int | None = None) -> None:
        super().__init__()
        self.pieces = pieces
        self.fail_after = fail_after
        self.calls: list = []

    async def stream(self, messages):
        self.calls.append(messages)
        for count, piece in enumerate(self.pieces):
            if self.fail_after == count:
                raise LLMError("injected failure")
            yield piece
        if self.fail_after == len(self.pieces):
            raise LLMError("injected failure")


BANK_Q = Question("t-1", "Python basics", "easy", "What is a list?", ("mutable",))


@pytest.fixture(autouse=True)
def one_question_bank(monkeypatch):
    """The opener is a bank question, so these tests pin it. Replies written by the LLM come from a follow-up turn."""
    monkeypatch.setattr("backend.main.question_bank", [BANK_Q])
    monkeypatch.setattr("backend.main.followups", 2)
    monkeypatch.setattr("backend.main.question_rng", random.Random(0))


@pytest.fixture
def wire(monkeypatch):
    def install(llm, tts):
        monkeypatch.setattr("backend.main.get_llm_provider", lambda: llm)
        monkeypatch.setattr("backend.main.get_tts_provider", lambda: tts)

    return install


def read_until(ws, kind, **fields):
    seen = []
    while True:
        msg = ws.receive_json()
        seen.append(msg)
        if msg["type"] == kind and all(msg.get(k) == v for k, v in fields.items()):
            return seen


def start_interview(ws):
    ws.receive_json()  # session
    ws.send_json({"type": "start", "topic": "Python basics", "difficulty": "easy"})
    return read_until(ws, "state", state="listening")


def answer(ws):
    ws.send_json({"type": "utterance_start", "sample_rate": SAMPLE_RATE})
    ws.send_bytes(ONE_SECOND)
    ws.send_json({"type": "utterance_end"})
    return read_until(ws, "state", state="listening")


def follow_up(ws):
    """Start, then answer once: the messages of the first turn that the LLM (not the bank) wrote."""
    start_interview(ws)
    return answer(ws)


def of_type(messages, kind):
    return [m for m in messages if m["type"] == kind]


def test_each_sentence_is_spoken_in_order_before_coach_done(wire):
    wire(ScriptedLLM(REPLY), FakeTTS())
    with client.websocket_connect("/ws") as ws:
        messages = follow_up(ws)
    speaks = of_type(messages, "speak")
    assert [(m["seq"], m["text"]) for m in speaks] == [(0, "One."), (1, "Two."), (2, "Three.")]
    kinds = [m["type"] for m in messages]
    assert kinds.index("coach_done") > max(i for i, k in enumerate(kinds) if k == "speak")


def test_bank_opener_is_spoken_by_tts_and_makes_no_llm_call(wire):
    llm = ScriptedLLM(REPLY)
    wire(llm, FakeTTS())
    with client.websocket_connect("/ws") as ws:
        messages = start_interview(ws)
    assert [m["text"] for m in of_type(messages, "speak")] == [BANK_Q.question]
    assert of_type(messages, "coach_done")[0]["provider"] == "bank"
    assert llm.calls == []


def test_speech_starts_before_the_llm_has_finished(wire):
    wire(ScriptedLLM(REPLY), FakeTTS())
    with client.websocket_connect("/ws") as ws:
        kinds = [m["type"] for m in follow_up(ws)]
    last_delta = max(i for i, k in enumerate(kinds) if k == "coach_delta")
    assert kinds.index("speak") < last_delta  # sentence 1 went out while pieces 2 and 3 were still arriving


def test_fake_tts_sends_labelled_playable_audio_with_timing(wire):
    wire(ScriptedLLM(REPLY), FakeTTS())
    with client.websocket_connect("/ws") as ws:
        speak = of_type(follow_up(ws), "speak")[0]
    assert (speak["provider"], speak["mime"]) == ("fake", "audio/wav")
    assert speak["tts_ms"] >= 0
    with wave.open(io.BytesIO(base64.b64decode(speak["audio_b64"]))) as wav:
        assert wav.getframerate() == 16000


def test_browser_tts_sends_text_only_and_no_synthesis_time(wire):
    wire(ScriptedLLM(REPLY), BrowserTTS())
    with client.websocket_connect("/ws") as ws:
        messages = follow_up(ws)
    speak = of_type(messages, "speak")[0]
    assert speak["provider"] == "browser"
    assert speak["tts_ms"] is None
    assert "audio_b64" not in speak and "mime" not in speak
    assert of_type(messages, "coach_done")[0]["tts_provider"] == "browser"


def test_unterminated_tail_is_spoken_on_flush(wire):
    wire(ScriptedLLM(["Hello ", "there"]), FakeTTS())
    with client.websocket_connect("/ws") as ws:
        speaks = of_type(follow_up(ws), "speak")
    assert [m["text"] for m in speaks] == ["Hello there"]


def test_coach_done_reports_stage_timings_and_slow_tts_shows_up(wire):
    wire(ScriptedLLM(REPLY), FakeTTS(delay_s=0.05))
    with client.websocket_connect("/ws") as ws:
        messages = follow_up(ws)
    done = of_type(messages, "coach_done")[0]
    assert done["tts_provider"] == "fake"
    assert 0 <= done["first_sentence_ms"] <= done["first_speak_ms"]
    assert done["first_speak_ms"] >= 45  # one 50 ms synthesis happened before the first speak message
    assert all(m["tts_ms"] >= 45 for m in of_type(messages, "speak"))


def test_tts_failure_is_reported_once_and_the_turn_still_completes(wire):
    llm = ScriptedLLM(REPLY)
    wire(llm, FakeTTS(fail_on_seq=1))
    with client.websocket_connect("/ws") as ws:
        messages = follow_up(ws)
        later = answer(ws)
    assert [m["seq"] for m in of_type(messages, "speak")] == [0]  # sentence 0 spoken, 1 failed, 2 skipped
    assert [m["reason"] for m in of_type(messages, "error")] == ["speech failed"]  # once, not per sentence
    assert of_type(messages, "coach_done")[0]["text"] == "One. Two. Three."  # text and history are complete
    assert messages[-1] == {"type": "state", "session_id": messages[-1]["session_id"], "state": "listening"}
    assert of_type(later, "coach_done")  # the session is still usable
    assistant = [m.text for m in llm.calls[1] if m.role == "assistant"]
    assert assistant == [BANK_Q.question, "One. Two. Three."]  # the complete reply is in history, not the unspoken remainder


def test_llm_failure_mid_reply_sends_error_and_does_not_speak_the_unfinished_tail(wire):
    wire(ScriptedLLM(["One. ", "Two"], fail_after=2), FakeTTS())
    with client.websocket_connect("/ws") as ws:
        start_interview(ws)
        messages = answer(ws)  # the failed turn rolls back to listening
    assert [m["text"] for m in of_type(messages, "speak")] == ["One."]  # already sent; client must cancel it on the error
    assert "coach reply failed" in [m["reason"] for m in of_type(messages, "error")]
    assert not of_type(messages, "coach_done")


def test_playback_started_is_accepted_silently(wire):
    wire(ScriptedLLM(REPLY), BrowserTTS())
    with client.websocket_connect("/ws") as ws:
        start_interview(ws)
        ws.send_json({"type": "playback_started", "stop_to_audio_ms": 1234.5})
        ws.send_json({"type": "ping"})
        assert ws.receive_json()["type"] == "echo"  # no reply was queued for playback_started


@pytest.mark.parametrize("bad", [None, "fast", -1, 600_000, True])
def test_playback_started_rejects_bad_values(wire, bad):
    wire(ScriptedLLM(REPLY), BrowserTTS())
    with client.websocket_connect("/ws") as ws:
        start_interview(ws)
        ws.send_json({"type": "playback_started", "stop_to_audio_ms": bad})
        assert ws.receive_json()["type"] == "error"
