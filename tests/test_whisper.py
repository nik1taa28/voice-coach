"""WhisperASR tests use a stub model: no model download, no network. They do need numpy (installed by
requirements-asr.txt), because the wrapper converts PCM16 bytes to a float32 array."""
import asyncio
import struct
from types import SimpleNamespace

import pytest

from backend.asr import ASRError, get_asr_provider
from backend.asr.whisper import WhisperASR


class StubModel:
    def __init__(self, segments=None, error=None):
        self.segments = segments or []
        self.error = error
        self.audio = None

    def transcribe(self, audio, **kwargs):
        self.audio = audio
        if self.error:
            return self._failing_generator(), None
        return iter(self.segments), None

    def _failing_generator(self):
        raise self.error
        yield  # makes this a generator: the error fires on iteration, like faster-whisper's lazy decoding


def make_asr(model):
    asr = WhisperASR()
    asr._model = model  # skip the real load
    return asr


def test_converts_pcm_to_float32_and_joins_segments():
    model = StubModel(segments=[SimpleNamespace(text=" hello "), SimpleNamespace(text="world ")])
    pcm = struct.pack("<hh", 16384, -32768)  # 0.5 and -1.0 after scaling

    result = asyncio.run(make_asr(model).transcribe(pcm, 16000))

    assert result.text == "hello world"
    assert result.provider == "faster-whisper"
    assert str(model.audio.dtype) == "float32"
    assert list(model.audio) == [0.5, -1.0]


def test_engine_error_during_lazy_decoding_becomes_asr_error():
    model = StubModel(error=RuntimeError("decode blew up"))
    with pytest.raises(ASRError, match="decode blew up"):
        asyncio.run(make_asr(model).transcribe(b"\x00\x00", 16000))


def test_provider_selection_returns_one_shared_lazy_instance(monkeypatch):
    monkeypatch.setenv("ASR_PROVIDER", "whisper")
    monkeypatch.setenv("ASR_MODEL", "tiny.en")
    first, second = get_asr_provider(), get_asr_provider()
    assert first is second  # shared, so the model loads once per process
    assert first.name == "faster-whisper"
    assert first._model is None  # selecting it must not trigger a load


def test_asr_error_reaches_the_websocket_client(monkeypatch):
    from fastapi.testclient import TestClient

    from backend.main import app

    class Broken:
        name = "broken"

        async def transcribe(self, pcm, sample_rate):
            raise ASRError("engine down")

    monkeypatch.setattr("backend.main.get_asr_provider", lambda: Broken())
    with TestClient(app).websocket_connect("/ws") as ws:
        session_id = ws.receive_json()["session_id"]
        ws.send_json({"type": "utterance_start", "sample_rate": 16000})
        ws.send_bytes(b"\x00\x00" * 16000)
        ws.send_json({"type": "utterance_end"})
        assert ws.receive_json() == {"type": "error", "session_id": session_id, "reason": "transcription failed"}
        ws.send_json({"ping": 1})  # connection survives an ASR failure
        assert ws.receive_json()["type"] == "echo"
