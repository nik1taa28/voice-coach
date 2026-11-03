import random

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.audio import SAMPLE_RATE
from backend.llm import FakeLLM, LLMError, Message
from backend.main import app
from backend.questions import Question

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


LIST_Q = Question("t-list", "Python basics", "easy", "What is a list?", ("mutable", "ordered"))
TUPLE_Q = Question("t-tuple", "Python basics", "easy", "What is a tuple?", ("immutable",))
OS_Q = Question("t-os", "OS: processes and scheduling", "hard", "What is a context switch?", ("saves state",))


@pytest.fixture(autouse=True)
def small_bank(monkeypatch):
    """A tiny fixed bank, so which question comes next never depends on the real file or on chance."""
    monkeypatch.setattr("backend.main.question_bank", [LIST_Q, TUPLE_Q, OS_Q])
    monkeypatch.setattr("backend.main.followups", 2)
    monkeypatch.setattr("backend.main.question_rng", random.Random(0))


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
    ws.send_json({"type": "start", "topic": "Python basics", "difficulty": "easy"})
    return read_until(ws, "state", state="listening")


def speak(ws):
    ws.send_json({"type": "utterance_start", "sample_rate": SAMPLE_RATE})
    ws.send_bytes(ONE_SECOND)
    ws.send_json({"type": "utterance_end"})


def types(messages):
    return [m["type"] for m in messages]


def bank_question_of(msgs):
    """The bank question a coach turn spoke, read from its coach_done."""
    return next(m for m in msgs if m["type"] == "coach_done")["text"]


def test_start_says_a_bank_question_word_for_word_and_makes_no_llm_call(llm):
    with client.websocket_connect("/ws") as ws:
        msgs = start_interview(ws)
        assert msgs[0]["type"] == "state" and msgs[0]["state"] == "thinking"
        deltas = [m["text"] for m in msgs if m["type"] == "coach_delta"]
        done = next(m for m in msgs if m["type"] == "coach_done")
        assert done["text"] in {LIST_Q.question, TUPLE_Q.question}
        assert "".join(deltas) == done["text"]
        assert done["provider"] == "bank"
        assert done["llm_ttft_ms"] is None and done["llm_total_ms"] is None  # no LLM ran, so no timing is invented
    assert llm.calls == []


def test_answer_gets_an_llm_follow_up_grounded_in_the_bank_question(llm):
    with client.websocket_connect("/ws") as ws:
        opener = bank_question_of(start_interview(ws))
        speak(ws)
        msgs = read_until(ws, "state", state="listening")
        assert types(msgs)[:2] == ["transcript", "state"]
        done = next(m for m in msgs if m["type"] == "coach_done")
        assert done["text"].startswith("Fake question 2") and done["provider"] == "fake"
    prompt = llm.calls[0]
    assert [m.role for m in prompt] == ["system", "user", "assistant", "user"]
    assert prompt[2].text == opener  # the bank question is in the history the LLM sees
    assert opener in prompt[0].text  # and in the system prompt, where trimming cannot remove it
    assert "mutable" in prompt[0].text or "immutable" in prompt[0].text  # with its key points
    assert prompt[-1].text == "fake transcript (1000 ms of audio)"


def test_after_two_follow_ups_the_next_turn_is_a_new_bank_question_without_the_llm(llm):
    with client.websocket_connect("/ws") as ws:
        first = bank_question_of(start_interview(ws))
        providers = []
        texts = []
        for _ in range(3):
            speak(ws)
            done = next(m for m in read_until(ws, "state", state="listening") if m["type"] == "coach_done")
            providers.append(done["provider"])
            texts.append(done["text"])
    assert providers == ["fake", "fake", "bank"]
    assert texts[2] in {LIST_Q.question, TUPLE_Q.question} and texts[2] != first  # a different question
    assert len(llm.calls) == 2  # only the two follow-ups used the LLM


def test_when_the_bank_is_used_up_the_coach_says_so_and_points_to_end(llm):
    with client.websocket_connect("/ws") as ws:
        start_interview(ws)
        texts = []
        for _ in range(6):  # 2 follow-ups, question 2, 2 follow-ups, then nothing left
            speak(ws)
            done = next(m for m in read_until(ws, "state", state="listening") if m["type"] == "coach_done")
            texts.append(done["text"])
    assert texts[-1].startswith("That was the last question")


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


def test_summary_request_carries_topic_difficulty_and_the_questions_asked(llm):
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()  # session
        ws.send_json({"type": "start", "topic": "OS: processes and scheduling", "difficulty": "hard"})
        read_until(ws, "state", state="listening")
        speak(ws)
        read_until(ws, "state", state="listening")
        ws.send_json({"type": "end"})
        read_until(ws, "summary")
    assert len(llm.calls) == 2  # one follow-up, then the summary (the opener came from the bank)
    summary_prompt = llm.calls[-1]
    system = summary_prompt[0].text
    assert "OS: processes and scheduling" in system and "hard" in system
    assert OS_Q.question in system and "saves state" in system  # the question asked and its key points
    assert summary_prompt[-1].role == "user" and "Practice next:" in summary_prompt[-1].text


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
    assert llm.calls == []  # the opener is a bank question and there was no answer to summarise


def test_invalid_start_is_rejected_and_can_be_retried(llm):
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "start", "topic": "Python basics", "difficulty": "insane"})
        assert "difficulty" in ws.receive_json()["reason"]
        ws.send_json({"type": "start", "topic": "Python basics", "difficulty": "easy"})
        assert read_until(ws, "state", state="listening")


def test_unknown_topic_is_rejected_and_can_be_retried(llm):
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "start", "topic": "Underwater basket weaving", "difficulty": "easy"})
        assert "unknown topic" in ws.receive_json()["reason"]
        ws.send_json({"type": "start", "topic": "Python basics", "difficulty": "easy"})
        assert read_until(ws, "state", state="listening")


def test_follow_up_failure_rolls_back_and_retry_works(monkeypatch):
    provider = RecordingLLM(fail_on=(1, 2))  # the answer's first try and its one retry both fail (M4 retry)
    monkeypatch.setattr("backend.main.get_llm_provider", lambda: provider)
    with client.websocket_connect("/ws") as ws:
        start_interview(ws)
        speak(ws)
        msgs = read_until(ws, "state", state="listening")  # the rollback state
        assert "coach reply failed" in [m.get("reason") for m in msgs]
        speak(ws)  # user answers again
        done = next(m for m in read_until(ws, "state", state="listening") if m["type"] == "coach_done")
        assert done["text"].startswith("Fake question 2")  # the failed answer was not kept
    assert [m.role for m in provider.calls[2]] == ["system", "user", "assistant", "user"]  # call 3: the second answer


def test_a_failed_follow_up_does_not_use_up_a_follow_up(monkeypatch):
    provider = RecordingLLM(fail_on=(1, 2))
    monkeypatch.setattr("backend.main.get_llm_provider", lambda: provider)
    with client.websocket_connect("/ws") as ws:
        start_interview(ws)
        speak(ws)
        read_until(ws, "state", state="listening")  # failed
        providers = []
        for _ in range(3):  # still gets its 2 follow-ups, then a new bank question
            speak(ws)
            done = next(m for m in read_until(ws, "state", state="listening") if m["type"] == "coach_done")
            providers.append(done["provider"])
    assert providers == ["fake", "fake", "bank"]


def test_topics_endpoint_lists_the_topics_and_levels(monkeypatch):
    response = client.get("/topics")
    assert response.status_code == 200
    assert response.json() == {"topics": ["Python basics", "OS: processes and scheduling"], "difficulties": ["easy", "medium", "hard"]}


def test_llm_unavailable_at_connect_reports_and_closes(monkeypatch):
    def broken():
        raise LLMError("GEMINI_API_KEY is not set")

    monkeypatch.setattr("backend.main.get_llm_provider", broken)
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "session"
        assert "GEMINI_API_KEY" in ws.receive_json()["reason"]
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
