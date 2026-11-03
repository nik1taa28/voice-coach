import asyncio
import json

import pytest

from backend.decision import MAX_TEXT_CHARS, Decision, DecisionError, parse_decision
from backend.llm import FakeLLM, Message
from backend.prompts import DECIDE_TAG, MAX_HISTORY_MESSAGES, build_decision_messages
from backend.questions import Question


def collect(llm, messages) -> list[str]:
    async def run() -> list[str]:
        return [piece async for piece in llm.stream(messages)]

    return asyncio.run(run())


POINTS = ("lists are mutable", "tuples are immutable")
BANK_Q = Question("t-1", "Python basics", "easy", "List vs tuple?", POINTS)


@pytest.mark.parametrize("action", ["follow_up", "move_on"])
def test_valid_decisions_parse(action):
    assert parse_decision(json.dumps({"action": action, "text": "  Good point.  "}), POINTS) == Decision(action, "Good point.")


def test_a_markdown_fence_is_accepted():
    raw = '```json\n{"action": "move_on", "text": "Thanks."}\n```'
    assert parse_decision(raw, POINTS) == Decision("move_on", "Thanks.")


@pytest.mark.parametrize(
    "raw, why",
    [
        ("Sure! Here is my follow-up question.", "not JSON"),
        ('["follow_up", "Why?"]', "not a JSON object"),
        ('{"action": "next_question", "text": "Why?"}', "action must be"),
        ('{"text": "Why?"}', "action must be"),
        ('{"action": "follow_up", "text": "   "}', "non-empty"),
        ('{"action": "follow_up", "text": 42}', "non-empty"),
        (json.dumps({"action": "follow_up", "text": "x" * (MAX_TEXT_CHARS + 1)}), "longer than"),
    ],
)
def test_invalid_decisions_are_rejected_with_a_reason(raw, why):
    with pytest.raises(DecisionError, match=why):
        parse_decision(raw, POINTS)


def test_text_that_reveals_a_key_point_is_rejected_case_insensitively():
    raw = json.dumps({"action": "follow_up", "text": "Remember: Lists Are Mutable. Why does that matter?"})
    with pytest.raises(DecisionError, match="reveals a key point"):
        parse_decision(raw, POINTS)


def test_decision_prompt_hides_the_key_points_and_asks_for_json_only():
    system = build_decision_messages("Python basics", [], BANK_Q)[0].text
    assert system.startswith(DECIDE_TAG)
    assert "List vs tuple?" in system and "lists are mutable; tuples are immutable" in system
    assert "Never read the key points out" in system and "ONLY a JSON object" in system


def test_decision_prompt_keeps_the_question_when_history_is_trimmed():
    history = []
    for i in range(20):
        history += [Message("assistant", f"coach {i}"), Message("user", f"answer {i}")]
    messages = build_decision_messages("Python basics", history, BANK_Q)
    assert len(messages) <= MAX_HISTORY_MESSAGES + 2 and messages[1].role == "user"
    assert "List vs tuple?" in messages[0].text


def test_fake_answers_a_decision_request_with_valid_json():
    history = [Message("assistant", "List vs tuple?"), Message("user", "One changes.")]
    raw = "".join(collect(FakeLLM(), build_decision_messages("Python basics", history, BANK_Q)))
    assert parse_decision(raw, POINTS) == Decision("follow_up", "Fake follow-up 1: tell me more about that.")


def test_fake_decision_reply_can_be_overridden_for_bad_output_tests():
    raw = "".join(collect(FakeLLM(decision_reply="not json at all"), build_decision_messages("x", [], BANK_Q)))
    with pytest.raises(DecisionError):
        parse_decision(raw, POINTS)
