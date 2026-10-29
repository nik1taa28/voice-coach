import asyncio

import pytest

from backend.llm import FakeLLM, LLMError, Message
from backend.prompts import MAX_HISTORY_MESSAGES, build_messages, build_summary_messages


def collect(llm, messages) -> list[str]:
    async def run() -> list[str]:
        return [piece async for piece in llm.stream(messages)]

    return asyncio.run(run())


def history(n_pairs: int) -> list[Message]:
    out = []
    for i in range(n_pairs):
        out += [Message("assistant", f"q{i}"), Message("user", f"a{i}")]
    return out


def test_fake_streams_in_several_pieces():
    pieces = collect(FakeLLM(), build_messages("Python", "easy", []))
    assert len(pieces) > 1
    assert "".join(pieces).startswith("Fake question 1")


def test_fake_is_deterministic_and_counts_answers():
    msgs = build_messages("Python", "easy", history(2))
    assert collect(FakeLLM(), msgs) == collect(FakeLLM(), msgs)
    assert "".join(collect(FakeLLM(), msgs)).startswith("Fake question 3")


def test_fake_summary_is_labelled_as_fake():
    text = "".join(collect(FakeLLM(), build_summary_messages(history(1))))
    assert text.startswith("Fake summary")


def test_fake_failure_raises_llm_error():
    with pytest.raises(LLMError):
        collect(FakeLLM(fail=True), build_messages("Python", "easy", []))


def test_prompt_contains_topic_and_difficulty():
    system = build_messages("Operating Systems", "hard", [])[0]
    assert system.role == "system"
    assert "Operating Systems" in system.text and "hard" in system.text


def test_prompt_first_non_system_role_is_user():
    assert build_messages("x", "easy", [])[1].role == "user"
    assert build_messages("x", "easy", history(1))[1].role == "user"


def test_prompt_trims_history_and_never_starts_mid_exchange():
    msgs = build_messages("x", "easy", history(20))
    recent = msgs[2:]
    assert len(recent) <= MAX_HISTORY_MESSAGES
    assert recent[0].role == "assistant"
    assert recent[-1].text == "a19"  # newest turn kept


def test_summary_sees_whole_history():
    assert len(build_summary_messages(history(20))) == 2 + 40
