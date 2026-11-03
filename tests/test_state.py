import pytest

from backend.llm.base import Message
from backend.session_state import InterviewSession, SessionError, State


def started() -> InterviewSession:
    s = InterviewSession()
    s.start("Python", "easy")
    s.add_coach("What is a list?")
    return s


def test_happy_path_alternates_roles():
    s = started()
    s.add_user("An ordered collection.")
    assert s.state is State.THINKING
    s.add_coach("Good. And a tuple?")
    assert s.state is State.LISTENING
    assert [m.role for m in s.history] == ["assistant", "user", "assistant"]


def test_answer_before_start_rejected():
    with pytest.raises(SessionError, match="cannot answer while idle"):
        InterviewSession().add_user("hi")


def test_answer_while_paused_rejected_and_state_unchanged():
    s = started()
    s.pause()
    with pytest.raises(SessionError, match="while paused"):
        s.add_user("hi")
    assert s.state is State.PAUSED
    assert len(s.history) == 1


def test_resume_only_from_paused():
    with pytest.raises(SessionError):
        started().resume()


def test_end_from_listening_and_paused_then_nothing_works():
    s = started()
    s.pause()
    s.end()
    assert s.state is State.ENDED
    with pytest.raises(SessionError):
        s.pause()


def test_start_twice_rejected():
    with pytest.raises(SessionError, match="cannot start"):
        started().start("Python", "easy")


@pytest.mark.parametrize("topic,difficulty", [("", "easy"), ("  ", "easy"), (None, "easy"), ("x" * 81, "easy"), ("Python", "insane")])
def test_start_validates_input(topic, difficulty):
    s = InterviewSession()
    with pytest.raises(SessionError):
        s.start(topic, difficulty)
    assert s.state is State.IDLE  # a rejected start changes nothing


def test_empty_answer_rejected():
    s = started()
    with pytest.raises(SessionError, match="empty"):
        s.add_user("   ")
    assert s.state is State.LISTENING


def test_fail_turn_drops_unanswered_user_message():
    s = started()
    s.add_user("my answer")
    s.fail_turn()
    assert s.state is State.LISTENING
    assert [m.role for m in s.history] == ["assistant"]


def test_fail_turn_on_opener_returns_to_idle():
    s = InterviewSession()
    s.start("Python", "easy")
    s.fail_turn()
    assert s.state is State.IDLE
    s.start("Python", "easy")  # can retry


def test_history_property_is_a_copy():
    s = started()
    s.history.append(Message("user", "sneaky"))
    assert len(s.history) == 1


def test_cancel_turn_drops_unanswered_user_message_and_allows_pause():
    s = started()
    s.add_user("my answer")
    s.cancel_turn()
    assert s.state is State.LISTENING
    assert [m.role for m in s.history] == ["assistant"]
    s.pause()  # what main.py does for Pause during a turn: cancel, then pause
    assert s.state is State.PAUSED


def test_cancel_turn_then_end_keeps_only_complete_exchanges():
    s = started()
    s.add_user("first")
    s.add_coach("follow-up")
    s.add_user("second, interrupted")
    s.cancel_turn()
    s.end()
    assert [m.text for m in s.history] == ["What is a list?", "first", "follow-up"]
    assert s.state is State.ENDED


def test_cancel_turn_on_opener_returns_to_idle_and_pause_is_then_rejected():
    s = InterviewSession()
    s.start("Python", "easy")
    s.cancel_turn()
    assert s.state is State.IDLE
    with pytest.raises(SessionError, match="cannot pause while idle"):
        s.pause()


@pytest.mark.parametrize("make_state", ["idle", "listening", "paused", "ended"])
def test_cancel_turn_rejected_unless_thinking(make_state):
    s = InterviewSession()
    if make_state != "idle":
        s = started()
    if make_state == "paused":
        s.pause()
    if make_state == "ended":
        s.end()
    before = (s.state, s.history)
    with pytest.raises(SessionError, match="cannot cancel a turn"):
        s.cancel_turn()
    assert (s.state, s.history) == before  # a finished reply is never discarded by a late cancel


# --- question bank planning (plan first, commit on success) ---

import random

from backend.questions import Question
from backend.session_state import TurnPlan


def q(qid: str, topic: str = "T", difficulty: str = "easy") -> Question:
    return Question(qid, topic, difficulty, f"Question {qid}?", ("point",))


BANK = [q("a"), q("b"), q("c")]


def bank_session(limit: int = 2) -> tuple[InterviewSession, list[TurnPlan]]:
    """Run turns the way main.py does: plan, then commit with add_coach. Returns the session and the plans in order."""
    s = InterviewSession()
    s.start("T", "easy")
    plans = []
    for turn in range(6):
        plan = s.plan_turn(BANK, limit, random.Random(turn))
        plans.append(plan)
        s.add_coach(f"turn {turn}", plan)
        if turn < 5:
            s.add_user("my answer")
    return s, plans


def test_plan_sequence_is_bank_then_followups_then_next_bank_question():
    _, plans = bank_session(limit=2)
    assert [p.kind for p in plans] == ["bank", "followup", "followup", "bank", "followup", "followup"]
    assert plans[1].question == plans[0].question == plans[2].question  # follow-ups stay on the question they probe
    assert plans[3].question != plans[0].question  # a question is never asked twice


def test_zero_followups_means_a_new_bank_question_every_turn():
    s = InterviewSession()
    s.start("T", "easy")
    seen = []
    for turn in range(3):
        plan = s.plan_turn(BANK, 0, random.Random(turn))
        assert plan.kind == "bank"
        seen.append(plan.question.id)
        s.add_coach("x", plan)
        if turn < 2:
            s.add_user("answer")
    assert sorted(seen) == ["a", "b", "c"]


def test_wrapup_when_the_bank_is_used_up():
    s = InterviewSession()
    s.start("T", "easy")
    for turn in range(3):
        s.add_coach("x", s.plan_turn(BANK, 0, random.Random(turn)))
        s.add_user("answer")
    plan = s.plan_turn(BANK, 0)
    assert plan == TurnPlan("wrapup", None)
    before = (s.current, s.followups_used, s.asked_questions)
    s.add_coach("That was the last one.", plan)
    assert (s.current, s.followups_used, s.asked_questions) == before  # wrapup commits nothing
    assert len(s.asked_questions) == 3


def test_plan_turn_changes_nothing():
    s = InterviewSession()
    s.start("T", "easy")
    s.plan_turn(BANK, 2)
    assert (s.current, s.followups_used, s.asked_questions) == (None, 0, [])


def test_failed_or_cancelled_turn_does_not_commit_the_plan():
    s = InterviewSession()
    s.start("T", "easy")
    s.add_coach("opening", s.plan_turn(BANK, 2, random.Random(1)))
    s.add_user("my answer")
    s.plan_turn(BANK, 2)  # planned, then the LLM fails
    s.fail_turn()
    assert s.followups_used == 0 and len(s.asked_questions) == 1  # the next attempt plans the same follow-up again


def test_failed_opener_leaves_nothing_behind_and_restart_resets():
    s = InterviewSession()
    s.start("T", "easy")
    s.plan_turn(BANK, 2)  # planned, then the opener fails
    s.fail_turn()
    assert s.state is State.IDLE and s.current is None and s.asked_questions == []
    s.start("T", "easy")  # retry after the failure
    assert s.plan_turn(BANK, 2).kind == "bank"


def test_questions_are_filtered_by_topic_and_difficulty():
    bank = [q("a", "T", "easy"), q("b", "Other", "easy"), q("c", "T", "hard")]
    s = InterviewSession()
    s.start("T", "hard")
    assert s.plan_turn(bank, 2).question.id == "c"
