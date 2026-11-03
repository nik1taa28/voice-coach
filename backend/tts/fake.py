import asyncio
import io
import math
import struct
import wave

from backend.tts.base import SpokenChunk, TTSError

SAMPLE_RATE = 16000
MS_PER_CHAR = 40  # fake "speaking speed": longer sentences give longer tones
MIN_MS, MAX_MS = 200, 2000
TONE_HZ = 440
AMPLITUDE = 4000  # of 32767: audible but quiet


def tone_wav(duration_ms: int) -> bytes:
    """A mono PCM16 WAV sine tone. Same input, same bytes."""
    frames = SAMPLE_RATE * duration_ms // 1000
    samples = (int(AMPLITUDE * math.sin(2 * math.pi * TONE_HZ * i / SAMPLE_RATE)) for i in range(frames))
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(b"".join(struct.pack("<h", s) for s in samples))
    return buffer.getvalue()


class FakeTTS:
    """Deterministic stand-in: plays a beep, NOT speech. The UI labels it fake."""

    name = "fake"

    def __init__(self, delay_s: float = 0.0, fail_on_seq: int | None = None, hang_on_seq: int | None = None) -> None:
        self.delay_s = delay_s  # simulates slow synthesis
        self.fail_on_seq = fail_on_seq  # raise TTSError for that sentence only (0 = the first)
        self.hang_on_seq = hang_on_seq  # never return for that sentence

    async def synthesize(self, text: str, seq: int) -> SpokenChunk:
        if self.fail_on_seq == seq:
            raise TTSError(f"fake TTS configured to fail on sentence {seq}")
        if self.hang_on_seq == seq:
            await asyncio.Event().wait()
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        duration_ms = min(MAX_MS, max(MIN_MS, MS_PER_CHAR * len(text)))
        return SpokenChunk(seq, text, tone_wav(duration_ms), "audio/wav")
