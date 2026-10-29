import asyncio
import logging
import threading
import time

from backend.asr.base import ASRError, Transcript

logger = logging.getLogger("voice_coach")


class WhisperASR:
    """Local faster-whisper on CPU. Needs `pip install -r requirements-asr.txt`."""

    name = "faster-whisper"

    def __init__(self, model_size: str = "tiny.en", compute_type: str = "int8") -> None:
        self.model_size = model_size
        self.compute_type = compute_type
        self._model = None
        self._load_lock = threading.Lock()  # transcribe runs in worker threads

    def _get_model(self):
        with self._load_lock:
            if self._model is None:
                from faster_whisper import WhisperModel  # lazy: importing this module needs no install

                started = time.perf_counter()
                self._model = WhisperModel(self.model_size, device="cpu", compute_type=self.compute_type)
                load_ms = (time.perf_counter() - started) * 1000
                logger.info("whisper model %s loaded in %.0f ms", self.model_size, load_ms)
            return self._model

    def _transcribe_blocking(self, pcm: bytes) -> str:
        import numpy as np  # comes with faster-whisper

        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        segments, _info = self._get_model().transcribe(audio, language="en", beam_size=1)
        # `segments` is a lazy generator: the real decoding happens while we iterate, so do it here, in the thread
        return " ".join(segment.text.strip() for segment in segments).strip()

    async def transcribe(self, pcm: bytes, sample_rate: int) -> Transcript:
        try:
            text = await asyncio.to_thread(self._transcribe_blocking, pcm)
        except (RuntimeError, ValueError, OSError) as exc:
            raise ASRError(f"faster-whisper failed: {exc}") from exc
        return Transcript(text=text, provider=self.name)
