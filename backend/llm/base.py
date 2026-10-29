from dataclasses import dataclass
from typing import AsyncIterator, Literal, Protocol


class LLMError(RuntimeError):
    """A provider failed to produce a reply. Providers wrap their engine-specific errors in this."""


@dataclass(frozen=True)
class Message:
    role: Literal["system", "user", "assistant"]
    text: str


class LLMProvider(Protocol):
    name: str

    def stream(self, messages: list[Message]) -> AsyncIterator[str]:
        """Yield the reply in pieces as they arrive. Raises LLMError on failure (possibly mid-stream)."""
        ...
