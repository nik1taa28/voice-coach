"""M4 reliability over the real /ws route: cancellation, deadlines, retry, cleanup. Slow/failing fakes, tiny timeouts."""
import asyncio
import random
import threading

import pytest
from fastapi.testclient import TestClient

from backend.asr import FakeASR
from backend.audio import SAMPLE_RATE
from backend.llm import FakeLLM
from backend.main import app
from backend.questions import Question
from backend.tts import FakeTTS

client = TestClient(app)
ONE_SECOND = b"\x00\x00" * SAMPLE_RATE


class ScriptLLM(FakeLLM):
    """Per call: 'ok' streams a follow_up decision in 3 pieces; 'hang' streams 2 pieces then stalls forever.
    `closed` counts finished streams. `stalled` is set just before a hang: follow-ups are no longer streamed to the client
    (the JSON is validated first), so the test cannot see deltas and waits on this instead. A threading.Event, because
    TestClient runs the app in another thread."""

    def __init__(self, *modes: str) -> None:
        super().__init__()
        self.modes = modes
        self.prompts: list = []
        self.closed = 0
        self.stalled = threading.Event()

    async def stream(self, messages):
        self.prompts.append(messages)
        mode = self.modes[min(len(self.prompts) - 1, len(self.modes) - 1)]
        try:
            yield '{"action": "follow_up", "text": "Alpha. '
            yield "Beta. "
            if mode == "hang":
                self.stalled.set()
                await asyncio.Event().wait()
            yield 'Gamma?"}'
        finally:
            self.closed += 1  # runs on completion, cancellation, or aclose(): proves the stream was released


class ExplodingLLM(FakeLLM):
    async def stream(self, messages):
        raise RuntimeError("a bug, not a provider failure")
        yield  # pragma: no cover


@pytest.fixture(autouse=True)
def one_question_bank(monkeypatch):
    """The opener is a bank question and never calls the LLM. Every LLM call in these tests is a decision about an answer.
    Two questions, so a fallback move_on has somewhere to go instead of ending the interview."""
    monkeypatch.setattr("backend.main.question_bank", [
        Question("t-1", "Python basics", "easy", "What is a list?", ("mutable",)),
        Question("t-2", "Python basics", "easy", "What is a tuple?", ("immutable",)),
    ])
    monkeypatch.setattr("backend.main.followups", 2)
    monkeypatch.setattr("backend.main.question_rng", random.Random(0))


@pytest.fixture
def wire(monkeypatch):
    def install(llm=None, asr=None, tts=None):
        monkeypatch.setattr("backend.main.get_llm_provider", lambda: llm or FakeLLM())
        monkeypatch.setattr("backend.main.get_asr_provider", lambda: asr or FakeASR())
        monkeypatch.setattr("backend.main.get_tts_provider", lambda: tts or FakeTTS())

    return install


def read_until(ws, kind, **fields):
    seen = []
    while True:
        msg = ws.receive_json()
        seen.append(msg)
        if msg["type"] == kind and all(msg.get(k) == v for k, v in fields.items()):
            return seen


def wait_until_stalled(llm: ScriptLLM) -> None:
    assert llm.stalled.wait(timeout=5), "the LLM never reached its stall point"


def start_interview(ws):
    ws.receive_json()  # session
    ws.send_json({"type": "start", "topic": "Python basics", "difficulty": "easy"})
    return read_until(ws, "state", state="listening")


def send_answer(ws):
    ws.send_json({"type": "utterance_start", "sample_rate": SAMPLE_RATE})
    ws.send_bytes(ONE_SECOND)
    ws.send_json({"type": "utterance_end"})


def answer_and_wait(ws):
    """Send an answer and read until the turn is over (the session is listening again)."""
    send_answer(ws)
    return read_until(ws, "state", state="listening")


def reasons(messages):
    return [m["reason"] for m in messages if m["type"] == "error"]


# --- cancellation: the receive loop keeps reading during a turn -----------------------------------------------------

def test_pause_during_reply_cancels_it_and_leaves_no_half_exchange(wire):
    llm = ScriptLLM("hang", "ok")  # the opener is a bank question, so the first LLM call is the first answer's follow-up
    wire(llm)
    with client.websocket_connect("/ws") as ws:
        start_interview(ws)
        send_answer(ws)
        wait_until_stalled(llm)  # the decision is now stalled mid-stream
        ws.send_json({"type": "pause"})
        msgs = read_until(ws, "state", state="paused")
        assert reasons(msgs) == ["turn cancelled"]
        ws.send_json({"type": "resume"})
        read_until(ws, "state", state="listening")
        send_answer(ws)
        read_until(ws, "coach_done")
    roles = [m.role for m in llm.prompts[1]]
    assert roles == ["system", "user", "assistant", "user"]  # the interrupted answer was dropped, not left dangling
    assert llm.closed == 2  # both streams were released, including the cancelled one


def test_end_during_reply_cancels_it_then_summarises_what_was_complete(wire):
    llm = ScriptLLM("hang")
    wire(llm)
    with client.websocket_connect("/ws") as ws:
        start_interview(ws)
        send_answer(ws)
        wait_until_stalled(llm)
        ws.send_json({"type": "end"})
        msgs = read_until(ws, "state", state="ended")
        assert reasons(msgs) == ["turn cancelled"]
        assert [m["state"] for m in msgs if m["type"] == "state"][-2:] == ["ending", "ended"]
        summary = next(m for m in msgs if m["type"] == "summary")
        assert summary["provider"] == "none"  # the only answer was interrupted, so no complete answers exist


def test_pause_during_asr_cancels_it(wire):
    wire(asr=FakeASR(hang=True))
    with client.websocket_connect("/ws") as ws:
        start_interview(ws)
        send_answer(ws)
        ws.send_json({"type": "pause"})  # the loop is free: ASR is hanging in a task, not in the receive loop
        msgs = read_until(ws, "state", state="paused")
        assert reasons(msgs) == ["turn cancelled"]


def test_pause_during_opener_returns_to_idle_and_reports_it(wire):
    wire(tts=FakeTTS(hang_on_seq=0))  # the bank opener cannot hang in the LLM, but its speech can
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "start", "topic": "Python basics", "difficulty": "easy"})
        read_until(ws, "state", state="thinking")
        ws.send_json({"type": "pause"})
        msgs = read_until(ws, "error", reason="cannot pause while idle")
        assert "turn cancelled" in reasons(msgs)
        assert {"type": "state", "session_id": msgs[0]["session_id"], "state": "idle"} in msgs  # client hears the rollback


def test_second_utterance_while_asr_is_running_is_rejected_as_busy(wire):
    wire(asr=FakeASR(hang=True))
    with client.websocket_connect("/ws") as ws:
        start_interview(ws)
        send_answer(ws)
        ws.send_json({"type": "utterance_start", "sample_rate": SAMPLE_RATE})
        assert ws.receive_json()["reason"].startswith("busy")


def test_pause_while_ending_does_not_kill_the_summary(wire):
    wire(ScriptLLM("ok"))
    with client.websocket_connect("/ws") as ws:
        start_interview(ws)
        send_answer(ws)
        read_until(ws, "state", state="listening")
        ws.send_json({"type": "end"})
        ws.send_json({"type": "pause"})  # rejected, and must not cancel the closing line or the summary
        rejected = {"cannot pause while ending", "cannot pause while ended"}  # depends on how far the ending got
        msgs = []
        while not (any(m["type"] == "summary" for m in msgs) and rejected & set(reasons(msgs))):
            msgs.append(ws.receive_json())  # the error and the summary may arrive in either order
        assert [m for m in msgs if m["type"] == "summary"][0]["provider"] == "fake"
        assert "turn cancelled" not in reasons(msgs)


# --- disconnect cleanup ---------------------------------------------------------------------------------------------

def test_disconnect_mid_reply_releases_the_llm_stream(wire):
    llm = ScriptLLM("hang")
    wire(llm)
    with client.websocket_connect("/ws") as ws:
        start_interview(ws)
        send_answer(ws)
        wait_until_stalled(llm)
    # leaving the block disconnects; the server's finally must cancel the turn, which closes the stream
    assert llm.closed == 1


# --- deadlines and retry --------------------------------------------------------------------------------------------

# A hung LLM now times out, is retried once, then falls back to move_on: see test_llm_ws.test_llm_timeout_falls_back_to_move_on.


def test_flaky_llm_is_retried_invisibly(wire):
    llm = FakeLLM(fail_first_n=1)
    wire(llm)
    with client.websocket_connect("/ws") as ws:
        start_interview(ws)
        msgs = answer_and_wait(ws)
        assert reasons(msgs) == []  # the client never saw the failed first attempt
        assert any(m["type"] == "coach_done" for m in msgs)
    assert llm.stream_starts == 2


def test_failure_after_first_piece_is_not_retried_and_falls_back(wire):
    llm = FakeLLM(fail_after_pieces=2)
    wire(llm)
    with client.websocket_connect("/ws") as ws:
        start_interview(ws)
        msgs = answer_and_wait(ws)
        assert reasons(msgs) == []  # the candidate hears the fallback acknowledgement, not an error
        assert next(m for m in msgs if m["type"] == "coach_done")["provider"] == "fallback"
    # The M4 rule (never retry after the first piece) is unchanged. Since decisions are no longer forwarded piece by piece,
    # a retry here would no longer repeat words to the client; relaxing the rule is left as a deliberate, separate change.
    assert llm.stream_starts == 1


def test_tts_hang_degrades_to_text_only(wire, monkeypatch):
    monkeypatch.setenv("TIMEOUT_TTS_S", "0.05")
    wire(tts=FakeTTS(hang_on_seq=0))
    with client.websocket_connect("/ws") as ws:
        msgs = start_interview(ws)
        assert reasons(msgs) == ["speech failed"]
        assert any(m["type"] == "coach_done" for m in msgs)  # the text reply still completed
        assert not any(m["type"] == "speak" for m in msgs)


def test_asr_hang_times_out_and_session_stays_usable(wire, monkeypatch):
    monkeypatch.setenv("TIMEOUT_ASR_S", "0.05")
    wire(asr=FakeASR(hang=True))
    with client.websocket_connect("/ws") as ws:
        start_interview(ws)
        send_answer(ws)
        assert ws.receive_json()["reason"] == "transcription timed out"
        ws.send_json({"type": "pause"})
        assert ws.receive_json()["state"] == "paused"


def test_summary_hang_times_out_but_session_still_ends(wire, monkeypatch):
    monkeypatch.setenv("TIMEOUT_SUMMARY_S", "0.05")
    llm = ScriptLLM("ok")
    wire(llm)
    with client.websocket_connect("/ws") as ws:
        start_interview(ws)
        send_answer(ws)
        read_until(ws, "state", state="listening")
        llm.modes = ("hang",)
        ws.send_json({"type": "end"})
        msgs = read_until(ws, "state", state="ended")  # the session still closes
        assert "summary timed out" in reasons(msgs)
        assert [m["state"] for m in msgs if m["type"] == "state"] == ["ending", "ended"]


def test_unexpected_error_in_a_turn_is_reported_not_swallowed(wire):
    wire(ExplodingLLM())
    with client.websocket_connect("/ws") as ws:
        start_interview(ws)
        msgs = answer_and_wait(ws)
        assert reasons(msgs) == ["internal error"]
        ws.send_json({"type": "ping"})  # the connection survived
        assert ws.receive_json()["type"] == "echo"
