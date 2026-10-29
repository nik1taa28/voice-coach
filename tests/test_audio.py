import asyncio

import pytest

from backend.asr import FakeASR, get_asr_provider
from backend.audio import MAX_UTTERANCE_MS, SAMPLE_RATE, AudioError, pcm16_duration_ms, validate_pcm, validate_sample_rate

ONE_SECOND = b"\x00\x00" * SAMPLE_RATE  # 16000 samples * 2 bytes


def test_duration_one_second():
    assert pcm16_duration_ms(ONE_SECOND) == 1000


def test_validate_accepts_normal_clip():
    validate_pcm(ONE_SECOND)  # no exception


def test_validate_rejects_empty():
    with pytest.raises(AudioError, match="no audio"):
        validate_pcm(b"")


def test_validate_rejects_odd_byte_count():
    with pytest.raises(AudioError, match="even"):
        validate_pcm(b"\x00\x00\x00")


def test_validate_rejects_too_long():
    too_long = b"\x00\x00" * (SAMPLE_RATE * (MAX_UTTERANCE_MS // 1000) + 1)
    with pytest.raises(AudioError, match="longer"):
        validate_pcm(too_long)


@pytest.mark.parametrize("bad", [8000, "16000", None, True])
def test_sample_rate_rejects_unsupported(bad):
    with pytest.raises(AudioError):
        validate_sample_rate(bad)


def test_sample_rate_accepts_16000():
    assert validate_sample_rate(16000) == 16000


def test_fake_asr_is_deterministic_and_labelled():
    asr = FakeASR()
    first = asyncio.run(asr.transcribe(ONE_SECOND, SAMPLE_RATE))
    second = asyncio.run(asr.transcribe(ONE_SECOND, SAMPLE_RATE))
    assert first == second
    assert first.text == "fake transcript (1000 ms of audio)"
    assert first.provider == "fake"


def test_get_asr_provider_defaults_to_fake(monkeypatch):
    monkeypatch.delenv("ASR_PROVIDER", raising=False)
    assert get_asr_provider().name == "fake"


def test_get_asr_provider_rejects_unknown(monkeypatch):
    monkeypatch.setenv("ASR_PROVIDER", "nope")
    with pytest.raises(ValueError, match="unknown ASR_PROVIDER"):
        get_asr_provider()
