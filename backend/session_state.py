"""Interview session state machine. Pure: no FastAPI, no I/O, so every transition is unit testable.
Time comes in as a `now` argument (a monotonic clock reading), so end conditions are tested without sleeping."""
import random
import time
from dataclasses import dataclass
from enum import Enum
from typing import Literal

from backend.llm.base import Message
from backend.questions import DIFFICULTIES, Question, pick_question

MAX_TOPIC_CHARS = 80
QUESTION_COUNTS = (5, 6, 8, 10)  # bank questions per interview the client may pick
DEFAULT_QUESTION_COUNT = 6
DEFAULT_TIME_LIMIT_S = 15 * 60.0  # wall time from Start, pauses included


class EndReason(str, Enum):
    QUESTION_LIMIT = "question_limit"
    TIME_LIMIT = "time_limit"
    BANK_EXHAUSTED = "bank_exhausted"
    MANUAL = "manual"


class SessionError(ValueError):
    """An illegal event for the current state, or invalid input. The message is safe to show the client."""


@dataclass(frozen=True)
class TurnPlan:
    """What the coach does next. Decided before the turn runs, applied to the session only if the turn succeeds."""

    kind: Literal["bank", "followup", "end"]  # bank: next bank question; followup: LLM probes the current one; end: closing line
    question: Question | None  # the bank question (new for "bank", current for "followup", None otherwise)
    end_reason: EndReason | None = None  # set only for "end"


class State(str, Enum):
    IDLE = "idle"
    LISTENING = "listening"  # waiting for the candidate's answer
    THINKING = "thinking"  # ASR / LLM work in progress
    PAUSED = "paused"
    ENDING = "ending"  # closing line said, summary being written; nothing else is accepted
    ENDED = "ended"


class InterviewSession:
    """One per WebSocket connection. Owns the state and the conversation history."""

    def __init__(self) -> None:
        self.state = State.IDLE
        self.topic = ""
        self.difficulty = ""
        self._history: list[Message] = []
        self.current: Question | None = None  # the bank question being discussed
        self.followups_used = 0  # LLM follow-ups already asked about `current`
        self._asked: list[Question] = []  # bank questions asked so far, in order
        self.question_count = DEFAULT_QUESTION_COUNT
        self.time_limit_s = DEFAULT_TIME_LIMIT_S
        self.started_at = 0.0  # monotonic clock reading at Start
        self.end_reason: EndReason | None = None

    @property
    def history(self) -> list[Message]:
        return list(self._history)  # copy, so callers cannot bypass the state checks

    @property
    def asked_questions(self) -> list[Question]:
        return list(self._asked)

    def _require(self, *allowed: State, event: str) -> None:
        if self.state not in allowed:
            raise SessionError(f"cannot {event} while {self.state.value}")

    def ensure_can_record(self) -> None:
        """Recording is allowed while listening, and while idle (M1 transcribe-only mode: no LLM is called)."""
        self._require(State.IDLE, State.LISTENING, event="record")

    def start(
        self,
        topic: object,
        difficulty: object,
        known_topics: list[str] | None = None,
        question_count: object = DEFAULT_QUESTION_COUNT,
        time_limit_s: float = DEFAULT_TIME_LIMIT_S,
        now: float | None = None,
    ) -> None:
        """idle -> thinking (the opening question is being prepared). With known_topics, the topic must be one of them."""
        self._require(State.IDLE, event="start")
        if not isinstance(topic, str) or not topic.strip():
            raise SessionError("topic must be a non-empty string")
        if len(topic.strip()) > MAX_TOPIC_CHARS:
            raise SessionError(f"topic longer than {MAX_TOPIC_CHARS} characters")
        if known_topics is not None and topic.strip() not in known_topics:
            raise SessionError("unknown topic: pick one from the list")
        if difficulty not in DIFFICULTIES:
            raise SessionError(f"difficulty must be one of {', '.join(DIFFICULTIES)}")
        # bool is an int subclass and 6.0 == 6, so check the type first, then membership
        if type(question_count) is not int or question_count not in QUESTION_COUNTS:
            raise SessionError(f"question_count must be one of {', '.join(map(str, QUESTION_COUNTS))}")
        if not time_limit_s > 0:
            raise SessionError("time limit must be greater than 0")
        self.topic = topic.strip()
        self.difficulty = difficulty
        self.current = None
        self.followups_used = 0
        self._asked = []
        self.question_count = question_count
        self.time_limit_s = time_limit_s
        self.started_at = time.monotonic() if now is None else now
        self.end_reason = None
        self.state = State.THINKING

    def time_up(self, now: float) -> bool:
        return now - self.started_at >= self.time_limit_s

    def plan_opening(self, bank: list[Question], rng: random.Random | None = None) -> TurnPlan:
        """The first bank question, or an immediate end if this topic and level has none."""
        question = pick_question(bank, self.topic, self.difficulty, set(), rng)
        if question is None:
            return TurnPlan("end", None, EndReason.BANK_EXHAUSTED)
        return TurnPlan("bank", question)

    def needs_decision(self, followups_per_question: int, now: float) -> bool:
        """Ask the LLM follow_up / move_on only while follow-ups are left and time is not up. Otherwise the code moves on
        without an LLM call: the model is never trusted to respect the cap."""
        return self.current is not None and self.followups_used < followups_per_question and not self.time_up(now)

    def plan_move_on(self, bank: list[Question], rng: random.Random | None = None, now: float | None = None) -> TurnPlan:
        """After move_on: the next bank question, or the end. Checked in this order: time, question count, bank."""
        now = time.monotonic() if now is None else now
        if self.time_up(now):
            return TurnPlan("end", None, EndReason.TIME_LIMIT)
        if len(self._asked) >= self.question_count:
            return TurnPlan("end", None, EndReason.QUESTION_LIMIT)
        question = pick_question(bank, self.topic, self.difficulty, {q.id for q in self._asked}, rng)
        if question is None:
            return TurnPlan("end", None, EndReason.BANK_EXHAUSTED)
        return TurnPlan("bank", question)

    def add_user(self, text: str) -> None:
        """listening -> thinking."""
        self._require(State.LISTENING, event="answer")
        if not text.strip():
            raise SessionError("empty answer")
        self._history.append(Message("user", text.strip()))
        self.state = State.THINKING

    def add_coach(self, text: str, plan: TurnPlan | None = None) -> None:
        """thinking -> listening. Called only with the COMPLETE reply, so history never holds a partial one.
        The plan is applied here and nowhere else: this is the commit point."""
        self._require(State.THINKING, event="record a coach reply")
        self._history.append(Message("assistant", text.strip()))
        self.state = State.LISTENING
        if plan is None:
            return
        if plan.kind == "end":  # the closing line was said: no more answers
            self.end_reason = plan.end_reason
            self.state = State.ENDING
            return
        if plan.kind == "bank" and plan.question is not None:
            self.current = plan.question
            self.followups_used = 0
            self._asked.append(plan.question)
        elif plan.kind == "followup":
            self.followups_used += 1

    def cancel_turn(self) -> None:
        """thinking -> listening (or idle if the opener was cancelled), for a turn that Pause / End / the time limit / an
        unexpected error / a disconnect interrupted. Drops the unanswered user message so roles still alternate.
        Callers check the state first: a turn that already finished is LISTENING and must keep its reply.
        (Provider failures no longer roll back: they fall back to move_on, see main.decide.)"""
        self._require(State.THINKING, event="cancel a turn")
        if not self._history:
            self.state = State.IDLE
            return
        if self._history[-1].role == "user":
            self._history.pop()
        self.state = State.LISTENING

    def pause(self) -> None:
        self._require(State.LISTENING, event="pause")
        self.state = State.PAUSED

    def resume(self) -> None:
        self._require(State.PAUSED, event="resume")
        self.state = State.LISTENING

    def begin_ending(self, reason: EndReason) -> None:
        """listening / paused -> ending: End pressed, or the time limit fired while no turn was running.
        (An automatic end during a turn goes through add_coach with an "end" plan instead.)"""
        self._require(State.LISTENING, State.PAUSED, event="end")
        self.end_reason = reason
        self.state = State.ENDING

    def finish(self) -> None:
        """ending -> ended, once the summary was sent (or failed). The session is closed."""
        self._require(State.ENDING, event="finish")
        self.state = State.ENDED
