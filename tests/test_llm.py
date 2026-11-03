import asyncio

import pytest

from backend.llm import FakeLLM, LLMError, Message
from backend.prompts import MAX_HISTORY_MESSAGES, SUMMARY_TAG, build_messages, build_summary_messages


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
    text = "".join(collect(FakeLLM(), build_summary_messages("Python", "easy", history(1))))
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
    assert len(build_summary_messages("Python", "easy", history(20))) == 2 + 40 + 1  # system + kickoff + history + the request


def ended_history(answers: int) -> list[Message]:
    """The shape of a real ended interview: the coach opens and always speaks last (End is only allowed while listening)."""
    out = [Message("assistant", "q0")]
    for i in range(answers):
        out += [Message("user", f"a{i}"), Message("assistant", f"q{i + 1}")]
    return out


def conversation_roles(messages: list[Message]) -> list[str]:
    return [m.role for m in messages if m.role != "system"]


def test_every_prompt_ends_with_a_user_turn_and_roles_alternate():
    """Real Gemini answered the summary prompt as if it were the coach, because that prompt ended with a coach turn. The fake
    LLM ignores message order, so only a test like this can catch it."""
    prompts = {
        "opener": build_messages("Python", "easy", []),
        "reply": build_messages("Python", "easy", history(2)),
        "summary": build_summary_messages("Python", "easy", ended_history(3)),
    }
    for name, messages in prompts.items():
        roles = conversation_roles(messages)
        assert roles[-1] == "user", name
        assert all(a != b for a, b in zip(roles, roles[1:])), f"{name}: roles do not alternate: {roles}"


def test_summary_request_asks_for_the_four_parts_and_forbids_questions():
    request = build_summary_messages("Python", "easy", ended_history(2))[-1]
    assert request.role == "user"
    for part in ("Summary:", "Strengths:", "Areas to improve:", "Practice next:"):
        assert part in request.text
    assert "Do not ask another question" in request.text


def test_summary_system_prompt_names_topic_and_difficulty():
    system = build_summary_messages("Operating Systems", "hard", ended_history(1))[0]
    assert system.role == "system"
    assert "Operating Systems" in system.text and "hard" in system.text


def test_summary_topic_with_braces_does_not_break_the_prompt():
    system = build_summary_messages("C++ {templates}", "easy", ended_history(1))[0]
    assert "C++ {templates}" in system.text  # values are substituted once, never re-interpreted as a template


def test_summary_system_prompt_keeps_the_tag_the_fake_llm_relies_on():
    assert SUMMARY_TAG in build_summary_messages("Python", "easy", ended_history(1))[0].text


def test_gemini_summary_payload_ends_with_a_user_turn():
    from backend.llm.gemini import GeminiLLM

    contents = GeminiLLM._payload(build_summary_messages("Python", "easy", ended_history(3)))["contents"]
    assert contents[-1]["role"] == "user"
    assert "Areas to improve:" in contents[-1]["parts"][0]["text"]


# --- question bank in the prompts ---

from backend.questions import Question

BANK_Q = Question("t-1", "Python basics", "easy", "What is a list?", ("mutable", "ordered"))


def test_follow_up_prompt_contains_the_question_and_its_key_points():
    system = build_messages("Python basics", "easy", [Message("assistant", "What is a list?"), Message("user", "A thing.")], BANK_Q)[0].text
    assert "What is a list?" in system and "mutable; ordered" in system


def test_follow_up_prompt_tells_the_model_not_to_reveal_the_key_points_and_to_ask_one_question():
    system = build_messages("Python basics", "easy", [], BANK_Q)[0].text
    assert "Never read them out" in system and "exactly ONE short follow-up" in system


def test_question_stays_in_the_prompt_even_when_history_is_trimmed():
    history = []
    for i in range(20):
        history += [Message("assistant", f"coach {i}"), Message("user", f"answer {i}")]
    messages = build_messages("Python basics", "easy", history, BANK_Q)
    assert "What is a list?" in messages[0].text  # the original question is long gone from the 12 kept messages
    assert all("coach 0" != m.text for m in messages)


def test_without_a_bank_question_the_generic_coach_prompt_is_used():
    system = build_messages("Python basics", "easy", [])[0].text
    assert "Ask ONE short question at a time" in system


def test_summary_prompt_lists_the_questions_asked_and_their_key_points():
    system = build_summary_messages("Python basics", "easy", [Message("assistant", "q"), Message("user", "a"), Message("assistant", "f")], [BANK_Q])[0].text
    assert "1. What is a list?" in system and "mutable; ordered" in system
    assert "do not credit points that were not said" in system


def test_summary_prompt_without_questions_is_unchanged():
    assert "key points" not in build_summary_messages("Python basics", "easy", [Message("assistant", "q")])[0].text
