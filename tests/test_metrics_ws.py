"""M5: the real /ws route feeds the metrics registry, and GET /metrics reports it."""
import asyncio
import random

import pytest
from fastapi.testclient import TestClient

from backend.asr import ASRError, FakeASR
from backend.audio import SAMPLE_RATE
from backend.llm import FakeLLM, LLMError
from backend.main import app
from backend.metrics import Metrics
from backend.questions import Question
from backend.tts import FakeTTS, TTSError

client = TestClient(app)
ONE_SECOND = b"\x00\x00" * SAMPLE_RATE


class FailingLLM(FakeLLM):
    async def stream(self, messages):
        raise LLMError("provider down")
        yield  # pragma: no cover


class HangingLLM(FakeLLM):
    async def stream(self, messages):
        await asyncio.Event().wait()
        yield "never"  # pragma: no cover


class SlowAfterFirstLLM(FakeLLM):
    async def stream(self, messages):
        yield "Alpha. "
        await asyncio.Event().wait()


class FailingASR(FakeASR):
    async def transcribe(self, pcm, sample_rate):
        raise ASRError("model crashed")


class FailingTTS(FakeTTS):
    async def synthesize(self, text, seq):
        raise TTSError("voice missing")


@pytest.fixture(autouse=True)
def one_question_bank(monkeypatch):
    """The opener is a bank question (no LLM). LLM stages are exercised by the first answer's follow-up."""
    monkeypatch.setattr("backend.main.question_bank", [Question("t-1", "Python basics", "easy", "What is a list?", ("mutable",))])
    monkeypatch.setattr("backend.main.followups", 2)
    monkeypatch.setattr("backend.main.question_rng", random.Random(0))


@pytest.fixture
def wire(monkeypatch):
    fresh = Metrics()
    monkeypatch.setattr("backend.main.metrics", fresh)

    def install(llm=None, asr=None, tts=None):
        monkeypatch.setattr("backend.main.get_llm_provider", lambda: llm or FakeLLM())
        monkeypatch.setattr("backend.main.get_asr_provider", lambda: asr or FakeASR())
        monkeypatch.setattr("backend.main.get_tts_provider", lambda: tts or FakeTTS())

    return install


def read_until(ws, kind, **fields):
    while True:
        msg = ws.receive_json()
        if msg["type"] == kind and all(msg.get(k) == v for k, v in fields.items()):
            return msg


def start_interview(ws):
    ws.receive_json()  # session
    ws.send_json({"type": "start", "topic": "Python basics", "difficulty": "easy"})


def send_answer(ws):
    ws.send_json({"type": "utterance_start", "sample_rate": SAMPLE_RATE})
    ws.send_bytes(ONE_SECOND)
    ws.send_json({"type": "utterance_end"})


def snapshot() -> dict:
    response = client.get("/metrics")
    assert response.status_code == 200
    return response.json()


def test_metrics_starts_empty(wire):
    wire()
    snap = snapshot()
    assert snap["error_rate"] is None
    assert all(stage["n"] == 0 and stage["p50"] is None for stage in snap["stages"].values())


def test_bank_opener_and_llm_follow_up_are_measured(wire):
    wire()
    with client.websocket_connect("/ws") as ws:
        start_interview(ws)
        read_until(ws, "coach_done")
        mid = snapshot()
        assert mid["counts"]["turn_ok"] == 1
        assert mid["stages"]["asr"]["n"] == 0  # the opener has no ASR stage
        for stage in ("first_sentence", "first_speak", "tts"):
            assert mid["stages"][stage]["n"] >= 1, stage
        for stage in ("llm_ttft", "llm_total"):
            assert mid["stages"][stage]["n"] == 0, stage  # a bank line made no LLM call: no number is recorded for it
        send_answer(ws)
        read_until(ws, "coach_done")
    done = snapshot()
    assert done["counts"]["turn_ok"] == 2
    assert done["stages"]["llm_ttft"]["n"] == 1 and done["stages"]["llm_total"]["n"] == 1
    assert done["stages"]["asr"]["n"] == 1
    assert done["error_rate"] == 0.0
    assert done["stages"]["llm_ttft"]["p50"] <= done["stages"]["llm_total"]["p95"]


def answer_after_opener(ws):
    start_interview(ws)
    read_until(ws, "coach_done")  # the bank opener
    send_answer(ws)


def test_llm_failure_counts_failed_and_records_no_latency(wire):
    wire(llm=FailingLLM())
    with client.websocket_connect("/ws") as ws:
        answer_after_opener(ws)
        read_until(ws, "error", reason="coach reply failed")
    snap = snapshot()
    assert snap["counts"]["turn_failed"] == 1
    assert snap["counts"]["turn_ok"] == 1  # the opener
    assert snap["stages"]["llm_total"]["n"] == 0  # a failed turn's partial timing must not look like a good sample
    assert snap["error_rate"] == 0.5


def test_llm_first_piece_timeout_counts_timeout(wire, monkeypatch):
    monkeypatch.setenv("TIMEOUT_LLM_FIRST_S", "0.05")
    wire(llm=HangingLLM())
    with client.websocket_connect("/ws") as ws:
        answer_after_opener(ws)
        read_until(ws, "error", reason="coach reply timed out")
    assert snapshot()["counts"]["turn_timeout"] == 1


def test_failure_after_first_piece_is_not_retried_and_counts_once(wire, monkeypatch):
    monkeypatch.setenv("TIMEOUT_LLM_TOTAL_S", "0.1")
    wire(llm=SlowAfterFirstLLM())
    with client.websocket_connect("/ws") as ws:
        answer_after_opener(ws)
        read_until(ws, "error", reason="coach reply timed out")
    counts = snapshot()["counts"]
    assert counts["turn_timeout"] == 1 and counts["turn_failed"] == 0


def test_asr_failure_counts_failed(wire):
    wire(asr=FailingASR())
    with client.websocket_connect("/ws") as ws:
        start_interview(ws)
        read_until(ws, "coach_done")
        send_answer(ws)
        read_until(ws, "error", reason="transcription failed")
    snap = snapshot()
    assert snap["counts"]["turn_failed"] == 1
    assert snap["counts"]["turn_ok"] == 1  # the opener
    assert snap["stages"]["asr"]["n"] == 0


def test_speech_failure_is_separate_from_turn_errors(wire):
    wire(tts=FailingTTS())
    with client.websocket_connect("/ws") as ws:
        start_interview(ws)
        read_until(ws, "coach_done")
    snap = snapshot()
    assert snap["counts"]["speech_failed"] == 1  # once per turn
    assert snap["counts"]["turn_ok"] == 1  # the text still arrived
    assert snap["error_rate"] == 0.0
    assert snap["stages"]["tts"]["n"] == 0


def test_cancelled_turn_is_not_an_error(wire):
    wire(llm=SlowAfterFirstLLM())
    with client.websocket_connect("/ws") as ws:
        start_interview(ws)
        read_until(ws, "coach_done")
        send_answer(ws)
        read_until(ws, "coach_delta", text="Alpha. ")  # the follow-up has started streaming
        ws.send_json({"type": "pause"})
        read_until(ws, "error", reason="turn cancelled")
    snap = snapshot()
    assert snap["counts"]["turn_cancelled"] == 1
    assert snap["error_rate"] == 0.0  # only the bank opener finished, and it was fine; the cancelled turn is not an error
