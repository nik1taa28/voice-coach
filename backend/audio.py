"""Pure helpers for raw PCM16 mono audio. No FastAPI or numpy, so they are trivial to unit test."""

SAMPLE_RATE = 16000  # Whisper's native rate; the browser downsamples to this before sending
BYTES_PER_SAMPLE = 2  # PCM16
MAX_UTTERANCE_MS = 60_000  # caps server memory per session (~1.9 MB at 16 kHz)


class AudioError(ValueError):
    """The client sent audio we refuse to process. The message is safe to show the client."""


def pcm16_duration_ms(data: bytes, sample_rate: int = SAMPLE_RATE) -> float:
    return len(data) / BYTES_PER_SAMPLE / sample_rate * 1000


def validate_sample_rate(sample_rate: object) -> int:
    # bool is a subclass of int, so reject it explicitly
    if isinstance(sample_rate, bool) or sample_rate != SAMPLE_RATE:
        raise AudioError(f"sample_rate must be {SAMPLE_RATE}")
    return SAMPLE_RATE


def validate_pcm(data: bytes, sample_rate: int = SAMPLE_RATE, max_ms: int = MAX_UTTERANCE_MS) -> None:
    if not data:
        raise AudioError("no audio received")
    if len(data) % BYTES_PER_SAMPLE:
        raise AudioError("PCM16 data must have an even number of bytes")
    if pcm16_duration_ms(data, sample_rate) > max_ms:
        raise AudioError(f"utterance longer than {max_ms // 1000}s")
