"""Interview session state machine. Pure: no FastAPI, no I/O, so every transition is unit testable."""
from enum import Enum

from backend.llm.base import Message

MAX_TOPIC_CHARS = 80
DIFFICULTIES = ("easy", "medium", "hard")


class SessionError(ValueError):
    """An illegal event for the current state, or invalid input. The message is safe to show the client."""


class State(str, Enum):
    IDLE = "idle"
    LISTENING = "listening"  # waiting for the candidate's answer
    THINKING = "thinking"  # ASR / LLM work in progress
    PAUSED = "paused"
    ENDED = "ended"


class InterviewSession:
    """One per WebSocket connection. Owns the state and the conversation history."""

    def __init__(self) -> None:
        self.state = State.IDLE
        self.topic = ""
        self.difficulty = ""
        self._history: list[Message] = []

    @property
    def history(self) -> list[Message]:
        return list(self._history)  # copy, so callers cannot bypass the state checks

    def _require(self, *allowed: State, event: str) -> None:
        if self.state not in allowed:
            raise SessionError(f"cannot {event} while {self.state.value}")

    def ensure_can_record(self) -> None:
        """Recording is allowed while listening, and while idle (M1 transcribe-only mode: no LLM is called)."""
        self._require(State.IDLE, State.LISTENING, event="record")

    def start(self, topic: object, difficulty: object) -> None:
        """idle -> thinking (the opening question is being generated)."""
        self._require(State.IDLE, event="start")
        if not isinstance(topic, str) or not topic.strip():
            raise SessionError("topic must be a non-empty string")
        if len(topic.strip()) > MAX_TOPIC_CHARS:
            raise SessionError(f"topic longer than {MAX_TOPIC_CHARS} characters")
        if difficulty not in DIFFICULTIES:
            raise SessionError(f"difficulty must be one of {', '.join(DIFFICULTIES)}")
        self.topic = topic.strip()
        self.difficulty = difficulty
        self.state = State.THINKING

    def add_user(self, text: str) -> None:
        """listening -> thinking."""
        self._require(State.LISTENING, event="answer")
        if not text.strip():
            raise SessionError("empty answer")
        self._history.append(Message("user", text.strip()))
        self.state = State.THINKING

    def add_coach(self, text: str) -> None:
        """thinking -> listening. Called only with the COMPLETE reply, so history never holds a partial one."""
        self._require(State.THINKING, event="record a coach reply")
        self._history.append(Message("assistant", text.strip()))
        self.state = State.LISTENING

    def fail_turn(self) -> None:
        """thinking -> listening (or idle if the opener failed). Drops the unanswered user message so roles still alternate."""
        self._require(State.THINKING, event="fail a turn")
        self._roll_back_turn()

    def cancel_turn(self) -> None:
        """Same rollback as fail_turn, for a turn the user (pause/end) or a disconnect interrupted. Callers check the state first:
        a turn that already finished is LISTENING and must keep its reply."""
        self._require(State.THINKING, event="cancel a turn")
        self._roll_back_turn()

    def _roll_back_turn(self) -> None:
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

    def end(self) -> None:
        self._require(State.LISTENING, State.PAUSED, event="end")
        self.state = State.ENDED
