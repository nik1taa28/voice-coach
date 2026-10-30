from dataclasses import dataclass
from typing import Protocol


class TTSError(RuntimeError):
    """A provider failed to speak a sentence. Providers wrap their engine-specific errors in this."""


@dataclass(frozen=True)
class SpokenChunk:
    seq: int  # position in the reply, so the client plays sentences in order
    text: str
    audio: bytes | None  # None = the client speaks `text` itself (browser speech)
    mime: str | None  # e.g. "audio/wav"; None when audio is None


class TTSProvider(Protocol):
    name: str

    async def synthesize(self, text: str, seq: int) -> SpokenChunk:
        """Prepare ONE complete sentence for playback. Not token-level streaming: it waits for the whole sentence."""
        ...
