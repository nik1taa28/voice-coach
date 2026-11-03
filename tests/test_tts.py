import asyncio
import io
import time
import wave

import pytest

from backend.tts import BrowserTTS, FakeTTS, TTSError, get_tts_provider


def frames(chunk) -> int:
    with wave.open(io.BytesIO(chunk.audio)) as wav:
        return wav.getnframes()


def test_fake_returns_a_valid_wav_and_is_deterministic():
    first = asyncio.run(FakeTTS().synthesize("Tell me more.", 3))
    again = asyncio.run(FakeTTS().synthesize("Tell me more.", 3))
    assert first == again
    assert (first.seq, first.text, first.mime) == (3, "Tell me more.", "audio/wav")
    with wave.open(io.BytesIO(first.audio)) as wav:
        assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (1, 2, 16000)
        assert wav.getnframes() == 16000 * (13 * 40) // 1000  # 13 chars * 40 ms = 520 ms


def test_fake_duration_is_clamped():
    assert frames(asyncio.run(FakeTTS().synthesize("Hi", 0))) == 16000 * 200 // 1000
    assert frames(asyncio.run(FakeTTS().synthesize("x" * 500, 0))) == 16000 * 2000 // 1000


def test_fake_fails_only_on_the_configured_sentence():
    async def run() -> None:
        tts = FakeTTS(fail_on_seq=1)
        await tts.synthesize("One.", 0)
        with pytest.raises(TTSError):
            await tts.synthesize("Two.", 1)
        await tts.synthesize("Three.", 2)

    asyncio.run(run())


def test_fake_delay_simulates_slow_synthesis():
    started = time.perf_counter()
    asyncio.run(FakeTTS(delay_s=0.05).synthesize("Hello there.", 0))
    assert time.perf_counter() - started >= 0.045  # asyncio.sleep may wake a hair early on some clocks


def test_browser_provider_sends_text_only():
    chunk = asyncio.run(BrowserTTS().synthesize("Hello.", 0))
    assert (chunk.text, chunk.audio, chunk.mime) == ("Hello.", None, None)


def test_names_identify_the_provider():
    assert FakeTTS().name == "fake"
    assert BrowserTTS().name == "browser"


def test_selection_defaults_to_fake(monkeypatch):
    monkeypatch.delenv("TTS_PROVIDER", raising=False)
    assert get_tts_provider().name == "fake"


def test_selection_by_env(monkeypatch):
    monkeypatch.setenv("TTS_PROVIDER", "browser")
    assert get_tts_provider().name == "browser"


def test_unknown_provider_is_rejected(monkeypatch):
    monkeypatch.setenv("TTS_PROVIDER", "piper")
    with pytest.raises(ValueError, match="unknown TTS_PROVIDER"):
        get_tts_provider()
