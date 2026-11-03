import asyncio

from backend.asr.base import ASRError, Transcript
from backend.audio import pcm16_duration_ms


class FakeASR:
    """Deterministic stand-in: same audio length gives the same text. Does NOT recognise speech."""

    name = "fake"

    def __init__(self, delay_s: float = 0.0, hang: bool = False, fail: bool = False) -> None:
        self.delay_s = delay_s  # lets tests simulate a slow engine
        self.hang = hang  # never returns
        self.fail = fail

    async def transcribe(self, pcm: bytes, sample_rate: int) -> Transcript:
        if self.fail:
            raise ASRError("fake ASR configured to fail")
        if self.hang:
            await asyncio.Event().wait()
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        ms = round(pcm16_duration_ms(pcm, sample_rate))
        return Transcript(text=f"fake transcript ({ms} ms of audio)", provider=self.name)
