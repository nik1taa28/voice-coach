import json
import random

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.audio import SAMPLE_RATE
from backend.decision import FALLBACK_ACK
from backend.llm import FakeLLM, LLMError, Message
from backend.main import CLOSING_TEXT, app
from backend.prompts import DECIDE_TAG
from backend.questions import Question
from backend.session_state import EndReason

client = TestClient(app)
ONE_SECOND = b"\x00\x00" * SAMPLE_RATE


class RecordingLLM(FakeLLM):
    """FakeLLM that remembers every prompt it was given, and can fail on chosen call numbers."""

    def __init__(self, fail_on: tuple[int, ...] = (), **fake_options) -> None:
        super().__init__(**fake_options)
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
BANK_QUESTIONS = {LIST_Q.question, TUPLE_Q.question}


@pytest.fixture(autouse=True)
def small_bank(monkeypatch):
    """A tiny fixed bank, so which question comes next never depends on the real file or on chance."""
    monkeypatch.setattr("backend.main.question_bank", [LIST_Q, TUPLE_Q, OS_Q])
    monkeypatch.setattr("backend.main.followups", 2)
    monkeypatch.setattr("backend.main.question_rng", random.Random(0))


def use_llm(monkeypatch, **options) -> RecordingLLM:
    provider = RecordingLLM(**options)
    monkeypatch.setattr("backend.main.get_llm_provider", lambda: provider)
    return provider


@pytest.fixture
def llm(monkeypatch):
    return use_llm(monkeypatch)


def read_until(ws, kind, **fields):
    """Collect messages up to and including the first one matching type (and fields)."""
    seen = []
    while True:
        msg = ws.receive_json()
        seen.append(msg)
        if msg["type"] == kind and all(msg.get(k) == v for k, v in fields.items()):
            return seen


def start_interview(ws, topic="Python basics", difficulty="easy", **extra):
    ws.receive_json()  # session
    ws.send_json({"type": "start", "topic": topic, "difficulty": difficulty, **extra})
    return read_until(ws, "state", state="listening")


def speak(ws):
    ws.send_json({"type": "utterance_start", "sample_rate": SAMPLE_RATE})
    ws.send_bytes(ONE_SECOND)
    ws.send_json({"type": "utterance_end"})


def answer(ws) -> tuple[dict, str]:
    """Answer once; return the coach's reply (coach_done) and the state that followed it (listening or ending)."""
    speak(ws)
    done = read_until(ws, "coach_done")[-1]
    return done, read_until(ws, "state")[-1]["state"]


def finish(ws) -> tuple[dict, list[dict]]:
    """From ENDING: read the summary and the final state. Returns the summary and every message seen."""
    msgs = read_until(ws, "state", state="ended")
    return next(m for m in msgs if m["type"] == "summary"), msgs


def types(messages):
    return [m["type"] for m in messages]


def bank_question_of(msgs):
    """The bank question a coach turn spoke, read from its coach_done."""
    return next(m for m in msgs if m["type"] == "coach_done")["text"]


# --- opening and follow-ups ---


def test_start_says_a_bank_question_word_for_word_and_makes_no_llm_call(llm):
    with client.websocket_connect("/ws") as ws:
        msgs = start_interview(ws)
        assert msgs[0]["type"] == "state" and msgs[0]["state"] == "thinking"
        deltas = [m["text"] for m in msgs if m["type"] == "coach_delta"]
        done = next(m for m in msgs if m["type"] == "coach_done")
        assert done["text"] in BANK_QUESTIONS
        assert "".join(deltas) == done["text"]
        assert done["provider"] == "bank"
        assert done["llm_ttft_ms"] is None and done["llm_total_ms"] is None  # no LLM ran, so no timing is invented
    assert llm.calls == []


def test_answer_gets_a_follow_up_from_a_json_decision_with_hidden_key_points(llm):
    with client.websocket_connect("/ws") as ws:
        opener = bank_question_of(start_interview(ws))
        done, state = answer(ws)
    assert (done["text"], done["provider"], state) == ("Fake follow-up 1: tell me more about that.", "fake", "listening")
    prompt = llm.calls[0]
    assert [m.role for m in prompt] == ["system", "user", "assistant", "user"]
    assert prompt[2].text == opener  # the bank question is in the history the LLM sees
    assert opener in prompt[0].text  # and in the system prompt, where trimming cannot remove it
    assert "mutable" in prompt[0].text or "immutable" in prompt[0].text  # its key points: hidden context for the model
    assert "ONLY a JSON object" in prompt[0].text
    assert prompt[-1].text == "fake transcript (1000 ms of audio)"


def test_follow_ups_are_capped_at_two_then_the_code_moves_on_without_the_llm(llm):
    with client.websocket_connect("/ws") as ws:
        first = bank_question_of(start_interview(ws))
        replies = [answer(ws)[0] for _ in range(3)]
    assert [r["provider"] for r in replies] == ["fake", "fake", "bank"]
    other = (BANK_QUESTIONS - {first}).pop()
    assert replies[2]["text"] == f"{FALLBACK_ACK} {other}"  # acknowledgement + the next bank question, one line
    assert len(llm.calls) == 2  # the cap is enforced in code: the model was not even asked the third time


def test_llm_move_on_goes_to_the_next_question_and_resets_the_follow_up_count(monkeypatch):
    provider = use_llm(monkeypatch, decision_reply=json.dumps({"action": "move_on", "text": "Good, that covers it."}))
    with client.websocket_connect("/ws") as ws:
        first = bank_question_of(start_interview(ws))
        done, state = answer(ws)
        other = (BANK_QUESTIONS - {first}).pop()
        assert (done["text"], state) == (f"Good, that covers it. {other}", "listening")
        answer(ws)  # about the second question: the model is asked again, since its follow-up count restarted
        finish(ws)  # move_on again with the bank used up: the interview ends and the summary is a third call
    assert [DECIDE_TAG in c[0].text for c in provider.calls] == [True, True, False]


def test_question_order_is_seeded_and_never_repeats(monkeypatch):
    bank = [Question(f"p-{i}", "Python basics", "easy", f"Python question {i}?", ("point",)) for i in range(8)]
    monkeypatch.setattr("backend.main.question_bank", bank)
    monkeypatch.setattr("backend.main.followups", 0)  # every answer moves on, so each turn is a new bank question
    use_llm(monkeypatch)

    def run() -> list[str]:
        monkeypatch.setattr("backend.main.question_rng", random.Random(42))
        with client.websocket_connect("/ws") as ws:
            asked = [bank_question_of(start_interview(ws, question_count=5))]
            for _ in range(4):
                done, _ = answer(ws)
                asked.append(done["text"].removeprefix(f"{FALLBACK_ACK} "))
        return asked

    first, second = run(), run()
    assert first == second  # same seed, same order
    assert len(first) == 5 == len(set(first))


# --- automatic and manual ending ---


def test_ends_after_the_chosen_number_of_questions(monkeypatch):
    bank = [Question(f"p-{i}", "Python basics", "easy", f"Python question {i}?", ("point",)) for i in range(8)]
    monkeypatch.setattr("backend.main.question_bank", bank)
    monkeypatch.setattr("backend.main.followups", 0)
    use_llm(monkeypatch)
    with client.websocket_connect("/ws") as ws:
        start_interview(ws, question_count=5)
        states = [answer(ws)[1] for _ in range(5)]
        summary, _ = finish(ws)
    assert states == ["listening"] * 4 + ["ending"]
    assert summary["end_reason"] == "question_limit" and summary["text"].startswith("Fake summary")


def test_ends_when_the_bank_runs_out(monkeypatch):
    monkeypatch.setattr("backend.main.followups", 0)
    use_llm(monkeypatch)
    with client.websocket_connect("/ws") as ws:
        start_interview(ws)  # default 6 questions, but this topic and level has only 2
        answer(ws)
        done, state = answer(ws)
        summary, msgs = finish(ws)
    assert state == "ending"
    assert done["text"] == f"{FALLBACK_ACK} {CLOSING_TEXT[EndReason.BANK_EXHAUSTED]}"
    assert summary["end_reason"] == "bank_exhausted"
    assert types(msgs)[-2:] == ["summary", "state"]  # the session closes only after the summary


def test_time_limit_ends_the_interview_while_waiting_for_an_answer(monkeypatch, llm):
    monkeypatch.setattr("backend.main.time_limit_s", 0.2)
    with client.websocket_connect("/ws") as ws:
        start_interview(ws)
        msgs = read_until(ws, "state", state="ended")  # the candidate never answers: the watchdog ends it anyway
    assert types(msgs)[0] == "state" and msgs[0]["state"] == "ending"
    closing = next(m for m in msgs if m["type"] == "coach_done")
    assert closing["text"] == CLOSING_TEXT[EndReason.TIME_LIMIT]
    summary = next(m for m in msgs if m["type"] == "summary")
    assert summary["end_reason"] == "time_limit" and summary["provider"] == "none"  # no answers, nothing to summarise


def test_manual_end_says_a_closing_line_then_summary_then_closes(llm):
    with client.websocket_connect("/ws") as ws:
        start_interview(ws)
        answer(ws)
        ws.send_json({"type": "end"})
        assert ws.receive_json()["state"] == "ending"
        summary, msgs = finish(ws)
        assert next(m for m in msgs if m["type"] == "coach_done")["text"] == CLOSING_TEXT[EndReason.MANUAL]
        assert summary["end_reason"] == "manual" and summary["text"].startswith("Fake summary")
        ws.send_json({"type": "pause"})
        assert "cannot pause while ended" in ws.receive_json()["reason"]


def test_end_without_answers_skips_the_llm(llm):
    with client.websocket_connect("/ws") as ws:
        start_interview(ws)
        ws.send_json({"type": "end"})
        summary, _ = finish(ws)
        assert summary["provider"] == "none"
    assert llm.calls == []  # the opener is a bank question and there was no answer to summarise


def test_summary_request_carries_topic_difficulty_and_the_questions_asked(llm):
    with client.websocket_connect("/ws") as ws:
        start_interview(ws, topic="OS: processes and scheduling", difficulty="hard")
        answer(ws)
        ws.send_json({"type": "end"})
        finish(ws)
    assert len(llm.calls) == 2  # one decision, then the summary (the opener came from the bank)
    summary_prompt = llm.calls[-1]
    system = summary_prompt[0].text
    assert "OS: processes and scheduling" in system and "hard" in system
    assert OS_Q.question in system and "saves state" in system  # the question asked and its key points
    assert summary_prompt[-1].role == "user" and "Practice next:" in summary_prompt[-1].text


# --- bad LLM output, timeouts and errors: always move_on with the canned acknowledgement ---


@pytest.mark.parametrize(
    "reply",
    [
        "Great answer! Can you tell me more?",  # prose, not JSON
        json.dumps({"action": "next_question", "text": "Why?"}),  # unknown action
        json.dumps({"action": "follow_up", "text": ""}),  # empty text
        json.dumps({"action": "follow_up", "text": "Is it immutable, mutable and ordered?"}),  # reveals a key point
    ],
)
def test_invalid_llm_output_falls_back_to_move_on(monkeypatch, reply):
    use_llm(monkeypatch, decision_reply=reply)
    with client.websocket_connect("/ws") as ws:
        first = bank_question_of(start_interview(ws))
        speak(ws)
        msgs = read_until(ws, "state", state="listening")
    done = next(m for m in msgs if m["type"] == "coach_done")
    assert done["text"] == f"{FALLBACK_ACK} {(BANK_QUESTIONS - {first}).pop()}"
    assert done["provider"] == "fallback"
    assert "error" not in types(msgs)  # the candidate just hears the next question


def test_llm_error_falls_back_to_move_on(monkeypatch):
    provider = use_llm(monkeypatch, fail_on=(1, 2))  # the first try and its one retry both fail
    with client.websocket_connect("/ws") as ws:
        start_interview(ws)
        done, state = answer(ws)
    assert done["text"].startswith(FALLBACK_ACK) and done["provider"] == "fallback" and state == "listening"
    assert len(provider.calls) == 2


def test_llm_timeout_falls_back_to_move_on(monkeypatch):
    monkeypatch.setenv("TIMEOUT_LLM_FIRST_S", "0.05")
    provider = use_llm(monkeypatch, hang=True)
    with client.websocket_connect("/ws") as ws:
        start_interview(ws)
        done, state = answer(ws)
    assert done["text"].startswith(FALLBACK_ACK) and done["provider"] == "fallback" and state == "listening"
    assert provider.stream_starts == 2  # first try + exactly one retry, then the fallback


# --- start validation and connection ---


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


@pytest.mark.parametrize(
    "change, error",
    [
        ({"difficulty": "insane"}, "difficulty"),
        ({"topic": "Underwater basket weaving"}, "unknown topic"),
        ({"question_count": 7}, "question_count must be one of 5, 6, 8, 10"),
    ],
)
def test_invalid_start_is_rejected_and_can_be_retried(llm, change, error):
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "start", "topic": "Python basics", "difficulty": "easy", **change})
        assert error in ws.receive_json()["reason"]
        ws.send_json({"type": "start", "topic": "Python basics", "difficulty": "easy"})
        assert read_until(ws, "state", state="listening")


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


# --- delivery feedback (measured, written by code) ---

import math
from array import array


def tone_pcm(seconds: float) -> bytes:
    n = round(seconds * SAMPLE_RATE)
    return array("h", (round(8000 * math.sin(2 * math.pi * 220 * i / SAMPLE_RATE)) for i in range(n))).tobytes()


def speak_pcm(ws, pcm: bytes) -> None:
    ws.send_json({"type": "utterance_start", "sample_rate": SAMPLE_RATE})
    ws.send_bytes(pcm)
    ws.send_json({"type": "utterance_end"})


def test_transcript_carries_measured_delivery_numbers(llm):
    with client.websocket_connect("/ws") as ws:
        start_interview(ws)
        speak_pcm(ws, tone_pcm(2) + b"\x00\x00" * (SAMPLE_RATE * 3 // 2) + tone_pcm(2))  # 2 s, 1.5 s gap, 2 s
        transcript = read_until(ws, "transcript")[-1]
    d = transcript["delivery"]
    assert d["pauses"] == 1 and abs(d["longest_pause_s"] - 1.5) < 0.06 and abs(d["speaking_s"] - 5.5) < 0.06
    assert d["words"] == 6  # the fake transcript's words: "fake transcript (5500 ms of audio)"
    assert d["fillers"] == 0


def test_summary_has_a_code_written_delivery_line_for_completed_answers_only(monkeypatch):
    provider = use_llm(monkeypatch)
    with client.websocket_connect("/ws") as ws:
        start_interview(ws)
        speak_pcm(ws, tone_pcm(2))
        read_until(ws, "state", state="listening")
        ws.send_json({"type": "end"})
        summary, _ = finish(ws)
    line = summary["delivery"]
    assert line.startswith("1 answer, 2 s on average; ") and "0 filler words" in line and "0 pauses" in line
    rate = int(line.split("; ")[1].split()[0])
    assert 177 <= rate <= 185  # 6 words in 2 s = 180, within one 30 ms frame (speaking time is counted in whole frames)
    assert all("words per minute" not in m.text for m in provider.calls[-1])  # the LLM never sees the numbers


def test_summary_without_answers_has_no_delivery_line(llm):
    with client.websocket_connect("/ws") as ws:
        start_interview(ws)
        ws.send_json({"type": "end"})
        summary, _ = finish(ws)
    assert summary["delivery"] is None
