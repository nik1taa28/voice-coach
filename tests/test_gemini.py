import asyncio
import json

import httpx
import pytest

from backend.llm import FakeLLM, LLMError, Message, get_llm_provider
from backend.llm.gemini import GeminiLLM

KEY = "test-key-123"
MESSAGES = [Message("system", "be brief"), Message("user", "start"), Message("assistant", "Q1?"), Message("user", "A1")]


def sse(*texts: str) -> bytes:
    events = [{"candidates": [{"content": {"parts": [{"text": t}]}}]} for t in texts]
    return b"".join(f"data: {json.dumps(e)}\r\n\r\n".encode() for e in events)


def collect(handler) -> list[str]:
    llm = GeminiLLM(api_key=KEY, transport=httpx.MockTransport(handler))

    async def run() -> list[str]:
        return [p async for p in llm.stream(MESSAGES)]

    return asyncio.run(run())


def test_streams_pieces_in_order():
    assert collect(lambda req: httpx.Response(200, content=sse("Hel", "lo ", "there"))) == ["Hel", "lo ", "there"]


def test_request_shape_and_key_not_in_url():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"], seen["key"], seen["body"] = str(req.url), req.headers.get("x-goog-api-key"), json.loads(req.content)
        return httpx.Response(200, content=sse("ok"))

    collect(handler)
    assert seen["key"] == KEY
    assert KEY not in seen["url"] and "alt=sse" in seen["url"]
    assert seen["body"]["systemInstruction"]["parts"][0]["text"] == "be brief"
    assert [c["role"] for c in seen["body"]["contents"]] == ["user", "model", "user"]


def test_http_error_becomes_llm_error_without_leaking_key():
    with pytest.raises(LLMError, match="429") as info:
        collect(lambda req: httpx.Response(429, json={"error": {"message": "quota"}}))
    assert KEY not in str(info.value)


def test_network_failure_becomes_llm_error():
    def handler(req):
        raise httpx.ConnectError("boom")

    with pytest.raises(LLMError, match="ConnectError"):
        collect(handler)


def test_malformed_json_becomes_llm_error():
    with pytest.raises(LLMError, match="malformed"):
        collect(lambda req: httpx.Response(200, content=b"data: {not json\r\n\r\n"))


def test_empty_reply_is_an_error():
    with pytest.raises(LLMError, match="empty"):
        collect(lambda req: httpx.Response(200, content=b'data: {"candidates": [{"finishReason": "SAFETY"}]}\r\n\r\n'))


def test_missing_key_rejected(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(LLMError, match="GEMINI_API_KEY"):
        GeminiLLM()


def test_provider_selection(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    assert isinstance(get_llm_provider(), FakeLLM)
    monkeypatch.setenv("LLM_PROVIDER", "nope")
    with pytest.raises(ValueError, match="unknown LLM_PROVIDER"):
        get_llm_provider()
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(LLMError):
        get_llm_provider()
