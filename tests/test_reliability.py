import asyncio

import pytest

from backend.llm import FakeLLM, LLMError
from backend.prompts import build_messages
from backend.reliability import StageTimeout, Timeouts, TurnRunner, stream_with_retry, with_deadline

MSGS = build_messages("Python", "easy", [])


def run(coro):
    return asyncio.run(coro)


def stream(llm, first_s=0.05, total_s=1.0, on_piece=None):
    return stream_with_retry(lambda: llm.stream(MSGS), first_s, total_s, on_piece, retry_delay_s=0.0)


# --- Timeouts -------------------------------------------------------------------------------------------------------

def test_timeouts_defaults_and_env_override():
    assert Timeouts.from_env({}) == Timeouts()
    assert Timeouts.from_env({"TIMEOUT_ASR_S": "0.5"}).asr_s == 0.5
    assert Timeouts.from_env({"TIMEOUT_ASR_S": "0.5"}).llm_first_s == Timeouts().llm_first_s


@pytest.mark.parametrize("bad", ["abc", "0", "-1", "nan"])
def test_timeouts_reject_bad_values(bad):
    with pytest.raises(ValueError, match="TIMEOUT_TTS_S"):
        Timeouts.from_env({"TIMEOUT_TTS_S": bad})


# --- with_deadline --------------------------------------------------------------------------------------------------

def test_with_deadline_returns_result():
    async def work():
        await asyncio.sleep(0)
        return 7

    assert run(with_deadline(work(), 1.0, "x")) == 7


def test_with_deadline_raises_stage_timeout_and_cancels_work():
    cancelled = []

    async def slow():
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    with pytest.raises(StageTimeout) as info:
        run(with_deadline(slow(), 0.02, "asr"))
    assert info.value.stage == "asr"
    assert cancelled == [True]


def test_with_deadline_does_not_mislabel_an_inner_timeout_error():
    async def inner_timeout():
        raise TimeoutError("from a socket, not our deadline")

    with pytest.raises(TimeoutError) as info:
        run(with_deadline(inner_timeout(), 1.0, "asr"))
    assert not isinstance(info.value, StageTimeout)


# --- stream_with_retry ----------------------------------------------------------------------------------------------

def test_stream_success_one_attempt():
    llm = FakeLLM()
    result = run(stream(llm))
    assert result.text.startswith("Fake question 1")
    assert result.attempts == 1 and llm.stream_starts == 1
    assert 0 <= result.ttft_ms <= result.total_ms


def test_flaky_before_first_piece_is_retried_once_and_succeeds():
    llm = FakeLLM(fail_first_n=1)
    result = run(stream(llm))
    assert result.attempts == 2 and llm.stream_starts == 2
    assert result.text.startswith("Fake question 1")


def test_two_failures_give_up_after_one_retry():
    llm = FakeLLM(fail_first_n=5)
    with pytest.raises(LLMError):
        run(stream(llm))
    assert llm.stream_starts == 2  # exactly one retry, never a loop


def test_hang_before_first_piece_times_out_then_retries_once():
    llm = FakeLLM(hang=True)
    with pytest.raises(StageTimeout) as info:
        run(stream(llm, first_s=0.02))
    assert info.value.stage == "llm_first_piece"
    assert llm.stream_starts == 2


def test_failure_after_first_piece_is_not_retried():
    llm = FakeLLM(fail_after_pieces=2)
    seen: list[str] = []

    async def on_piece(piece):
        seen.append(piece)

    with pytest.raises(LLMError):
        run(stream(llm, on_piece=on_piece))
    assert llm.stream_starts == 1  # a retry would repeat text the client already received
    assert len(seen) == 2


def test_stall_after_first_piece_hits_total_deadline_and_is_not_retried():
    llm = FakeLLM(hang_after_pieces=2)
    with pytest.raises(StageTimeout) as info:
        run(stream(llm, first_s=1.0, total_s=0.05))
    assert info.value.stage == "llm_total"
    assert llm.stream_starts == 1


def test_empty_stream_counts_as_failure_before_output_and_is_retried():
    calls = []

    async def empty():
        calls.append(1)
        return
        yield  # pragma: no cover  (makes this an async generator)

    with pytest.raises(LLMError, match="empty"):
        run(stream_with_retry(empty, 0.05, 1.0, retry_delay_s=0.0))
    assert len(calls) == 2


def test_on_piece_error_is_not_retried():
    llm = FakeLLM()

    async def broken_send(piece):
        raise ConnectionError("client went away")

    with pytest.raises(ConnectionError):
        run(stream(llm, on_piece=broken_send))
    assert llm.stream_starts == 1


def test_cancellation_closes_the_stream():
    closed = []

    async def gen():
        try:
            yield "a "
            await asyncio.Event().wait()
        finally:
            closed.append(True)

    async def main():
        task = asyncio.create_task(stream_with_retry(gen, 1.0, 5.0))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    run(main())
    assert closed == [True]  # the upstream connection would be released, not left open


# --- TurnRunner -----------------------------------------------------------------------------------------------------

def test_turn_runner_cancel_stops_the_task_and_runs_its_cleanup():
    cleaned = []

    async def work():
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.append(True)

    async def main():
        runner = TurnRunner()
        runner.start(work())
        await asyncio.sleep(0)
        assert runner.active
        assert await runner.cancel() is True
        assert not runner.active

    run(main())
    assert cleaned == [True]


def test_turn_runner_cancel_when_idle_is_a_noop():
    async def main():
        runner = TurnRunner()
        assert await runner.cancel() is False
        runner.start(asyncio.sleep(0))
        await asyncio.sleep(0.01)  # finished on its own
        assert await runner.cancel() is False

    run(main())


def test_turn_runner_refuses_a_second_task():
    async def main():
        runner = TurnRunner()
        runner.start(asyncio.sleep(10))
        with pytest.raises(RuntimeError, match="already running"):
            runner.start(asyncio.sleep(0))
        await runner.cancel()

    run(main())
