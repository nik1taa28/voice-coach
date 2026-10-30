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
