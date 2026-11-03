"""Pure helpers for session handling. No FastAPI imports, so they are trivial to unit test."""
import base64
import uuid
from datetime import datetime, timezone

from backend.asr.base import Transcript
from backend.audio import MAX_UTTERANCE_MS, AudioError, pcm16_duration_ms, validate_pcm, validate_sample_rate
from backend.tts.base import SpokenChunk


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


def build_state(session_id: str, state: str) -> dict:
    return {"type": "state", "session_id": session_id, "state": state}


def build_coach_delta(session_id: str, text: str) -> dict:
    return {"type": "coach_delta", "session_id": session_id, "text": text}


def build_coach_done(
    session_id: str,
    text: str,
    provider: str,
    ttft_ms: float | None,
    total_ms: float | None,
    tts_provider: str | None = None,
    first_sentence_ms: float | None = None,
    first_speak_ms: float | None = None,
) -> dict:
    return {
        "type": "coach_done",
        "session_id": session_id,
        "text": text,
        "provider": provider,
        "llm_ttft_ms": _round_or_none(ttft_ms),  # time to first token; None when the line came from the question bank (no LLM call)
        "llm_total_ms": _round_or_none(total_ms),  # time to last token; TTS runs after it, so it is not included
        "tts_provider": tts_provider,
        "first_sentence_ms": _round_or_none(first_sentence_ms),  # turn start -> first complete sentence
        "first_speak_ms": _round_or_none(first_speak_ms),  # turn start -> first speak message sent (None if nothing spoken)
    }


def build_speak(session_id: str, chunk: SpokenChunk, provider: str, tts_ms: float | None) -> dict:
    """One sentence to play. `audio_b64` is absent when the browser should speak `text` itself."""
    message = {
        "type": "speak",
        "session_id": session_id,
        "seq": chunk.seq,
        "text": chunk.text,
        "provider": provider,
        "tts_ms": _round_or_none(tts_ms),  # None when the server did no synthesis (browser speech)
    }
    if chunk.audio is not None:
        message["mime"] = chunk.mime
        message["audio_b64"] = base64.b64encode(chunk.audio).decode("ascii")
    return message


def _round_or_none(value: float | None) -> float | None:
    return None if value is None else round(value, 1)


def build_summary(
    session_id: str,
    text: str,
    provider: str,
    ttft_ms: float | None,
    total_ms: float | None,
    end_reason: str | None = None,
    delivery: str | None = None,
) -> dict:
    return {
        "type": "summary",
        "session_id": session_id,
        "text": text,
        "provider": provider,
        "end_reason": end_reason,  # question_limit / time_limit / bank_exhausted / manual
        "delivery": delivery,  # measured delivery line written by code (None without answers), never by the LLM
        "llm_ttft_ms": None if ttft_ms is None else round(ttft_ms, 1),
        "llm_total_ms": None if total_ms is None else round(total_ms, 1),
    }


class UtteranceBuffer:
    """Collects the audio chunks of one utterance. One instance per WebSocket connection."""

    def __init__(self) -> None:
        self._pcm = bytearray()
        self._sample_rate: int | None = None
        self._overflowed = False

    def discard(self) -> None:
        """Throw away a half-recorded utterance (used on pause/end so stale audio cannot leak into the next one)."""
        self._reset()

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
