"""Pure helpers for session handling. No FastAPI imports, so they are trivial to unit test."""
import uuid
from datetime import datetime, timezone

from backend.asr.base import Transcript
from backend.audio import MAX_UTTERANCE_MS, AudioError, pcm16_duration_ms, validate_pcm, validate_sample_rate


def new_session_id() -> str:
    return uuid.uuid4().hex


def server_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_echo(session_id: str, message: dict) -> dict:
    return {
        "type": "echo",
        "session_id": session_id,
        "data": message,
        "server_ts": server_timestamp(),
    }


def build_error(session_id: str, reason: str) -> dict:
    return {"type": "error", "session_id": session_id, "reason": reason}


def build_transcript(session_id: str, transcript: Transcript, audio_ms: float, asr_ms: float) -> dict:
    return {
        "type": "transcript",
        "session_id": session_id,
        "text": transcript.text,
        "provider": transcript.provider,
        "audio_ms": round(audio_ms, 1),
        "asr_ms": round(asr_ms, 1),
        "rtf": round(asr_ms / audio_ms, 3),  # real-time factor: below 1 = faster than real time
    }


class UtteranceBuffer:
    """Collects the audio chunks of one utterance. One instance per WebSocket connection."""

    def __init__(self) -> None:
        self._pcm = bytearray()
        self._sample_rate: int | None = None
        self._overflowed = False

    def _reset(self) -> None:
        self._pcm = bytearray()
        self._sample_rate = None
        self._overflowed = False

    def start(self, sample_rate: object) -> None:
        was_active = self._sample_rate is not None
        self._reset()
        rate = validate_sample_rate(sample_rate)
        if was_active:
            raise AudioError("utterance already in progress; discarded it, send utterance_start again")
        self._sample_rate = rate

    def add(self, chunk: bytes) -> None:
        if self._sample_rate is None:
            raise AudioError("audio received before utterance_start")
        if self._overflowed:
            return  # already reported; drop silently until utterance_end
        self._pcm.extend(chunk)
        if pcm16_duration_ms(self._pcm, self._sample_rate) > MAX_UTTERANCE_MS:
            self._pcm = bytearray()  # free the memory now
            self._overflowed = True
            raise AudioError(f"utterance longer than {MAX_UTTERANCE_MS // 1000}s")

    def finish(self) -> tuple[bytes, int] | None:
        """Return (pcm, sample_rate), or None if the utterance already overflowed (already reported)."""
        if self._sample_rate is None:
            raise AudioError("utterance_end without utterance_start")
        pcm, rate, overflowed = bytes(self._pcm), self._sample_rate, self._overflowed
        self._reset()
        if overflowed:
            return None
        validate_pcm(pcm, rate)
        return pcm, rate
