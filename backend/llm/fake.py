import asyncio
import json
from typing import AsyncIterator

from backend.llm.base import LLMError, Message
from backend.prompts import DECIDE_TAG, SUMMARY_TAG


class FakeLLM:
    """Deterministic stand-in: the reply depends only on how many answers came before. Does NOT understand anything."""

    name = "fake"

    def __init__(
        self,
        delay_s: float = 0.0,
        fail: bool = False,
        hang: bool = False,
        fail_first_n: int = 0,
        fail_after_pieces: int | None = None,
        hang_after_pieces: int | None = None,
        decision_reply: str | None = None,
    ) -> None:
        self.delay_s = delay_s  # per-piece delay, so tests can simulate a slow model
        self.fail = fail
        self.hang = hang  # never produces a first piece
        self.fail_first_n = fail_first_n  # the first N stream() calls fail, later ones succeed (a flaky upstream)
        self.fail_after_pieces = fail_after_pieces  # raise after yielding this many pieces (failure mid-reply)
        self.hang_after_pieces = hang_after_pieces  # stall after yielding this many pieces
        self.decision_reply = decision_reply  # raw text for decision requests, so tests can send bad JSON or a move_on
        self.stream_starts = 0  # how many times stream() was started, so tests can assert on retries (not `calls`: test subclasses use that name)

    async def stream(self, messages: list[Message]) -> AsyncIterator[str]:
        self.stream_starts += 1
        if self.fail or self.stream_starts <= self.fail_first_n:
            raise LLMError("fake LLM configured to fail")
        if self.hang:
            await asyncio.Event().wait()
        if SUMMARY_TAG in messages[0].text:
            text = "Fake summary: this is placeholder text, not real feedback."
        elif DECIDE_TAG in messages[0].text:
            answers = sum(1 for m in messages if m.role == "user") - 1  # minus the kickoff message
            text = self.decision_reply if self.decision_reply is not None else json.dumps(
                {"action": "follow_up", "text": f"Fake follow-up {answers}: tell me more about that."}
            )
        else:
            answers = sum(1 for m in messages if m.role == "user") - 1  # minus the kickoff message
            text = f"Fake question {answers + 1}: tell me more about that."
        for index, word in enumerate(text.split(" ")):
            if self.fail_after_pieces == index:
                raise LLMError("fake LLM configured to fail mid-reply")
            if self.hang_after_pieces == index:
                await asyncio.Event().wait()
            if self.delay_s:
                await asyncio.sleep(self.delay_s)
            yield word + " "
