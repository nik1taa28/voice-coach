import asyncio

from backend.asr.base import Transcript
from backend.audio import pcm16_duration_ms


class FakeASR:
    """Deterministic stand-in: same audio length gives the same text. Does NOT recognise speech."""

    name = "fake"

    def __init__(self, delay_s: float = 0.0) -> None:
        self.delay_s = delay_s  # lets tests simulate a slow engine

    async def transcribe(self, pcm: bytes, sample_rate: int) -> Transcript:
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        ms = round(pcm16_duration_ms(pcm, sample_rate))
        return Transcript(text=f"fake transcript ({ms} ms of audio)", provider=self.name)
