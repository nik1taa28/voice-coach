import os

from backend.tts.base import SpokenChunk, TTSError, TTSProvider
from backend.tts.browser import BrowserTTS
from backend.tts.fake import FakeTTS

__all__ = ["SpokenChunk", "TTSError", "TTSProvider", "FakeTTS", "BrowserTTS", "get_tts_provider"]


def get_tts_provider() -> TTSProvider:
    """Pick the provider from TTS_PROVIDER (default: fake, so demo mode needs nothing installed)."""
    choice = os.environ.get("TTS_PROVIDER", "fake")
    if choice == "fake":
        return FakeTTS()
    if choice == "browser":
        return BrowserTTS()
    raise ValueError(f"unknown TTS_PROVIDER {choice!r} (available: fake, browser)")
