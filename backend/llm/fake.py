import asyncio
from typing import AsyncIterator

from backend.llm.base import LLMError, Message
from backend.prompts import SUMMARY_TAG


class FakeLLM:
    """Deterministic stand-in: the reply depends only on how many answers came before. Does NOT understand anything."""

    name = "fake"

    def __init__(self, delay_s: float = 0.0, fail: bool = False) -> None:
        self.delay_s = delay_s  # per-piece delay, so tests can simulate a slow model
        self.fail = fail

    async def stream(self, messages: list[Message]) -> AsyncIterator[str]:
        if self.fail:
            raise LLMError("fake LLM configured to fail")
        if SUMMARY_TAG in messages[0].text:
            text = "Fake summary: this is placeholder text, not real feedback."
        else:
            answers = sum(1 for m in messages if m.role == "user") - 1  # minus the kickoff message
            text = f"Fake question {answers + 1}: tell me more about that."
        for word in text.split(" "):
            if self.delay_s:
                await asyncio.sleep(self.delay_s)
            yield word + " "
