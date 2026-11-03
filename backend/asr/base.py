from dataclasses import dataclass
from typing import Protocol


class ASRError(RuntimeError):
    """A provider failed to transcribe. Providers wrap their engine-specific errors in this."""


@dataclass(frozen=True)
class Transcript:
    text: str
    provider: str  # shown to the client, so a fake is never mistaken for a real engine


class ASRProvider(Protocol):
    name: str

    async def transcribe(self, pcm: bytes, sample_rate: int) -> Transcript:
        """Transcribe one complete utterance (PCM16 mono). Not streaming: needs the whole clip."""
        ...
