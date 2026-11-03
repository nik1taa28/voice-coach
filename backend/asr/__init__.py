import os

from backend.asr.base import ASRError, ASRProvider, Transcript
from backend.asr.fake import FakeASR

__all__ = ["ASRError", "ASRProvider", "Transcript", "FakeASR", "get_asr_provider"]


_whisper_instances: dict[str, ASRProvider] = {}  # one per model size, so the model loads once per process


def get_asr_provider() -> ASRProvider:
    """Pick the provider from ASR_PROVIDER (default: fake, so demo mode needs nothing installed)."""
    choice = os.environ.get("ASR_PROVIDER", "fake")
    if choice == "fake":
        return FakeASR()
    if choice == "whisper":
        from backend.asr.whisper import WhisperASR

        model_size = os.environ.get("ASR_MODEL", "tiny.en")
        if model_size not in _whisper_instances:
            _whisper_instances[model_size] = WhisperASR(model_size)
        return _whisper_instances[model_size]
    raise ValueError(f"unknown ASR_PROVIDER {choice!r} (available: fake, whisper)")
