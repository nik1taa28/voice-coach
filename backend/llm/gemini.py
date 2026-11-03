import json
import os
from typing import AsyncIterator

import httpx

from backend.llm.base import LLMError, Message

BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"
DEFAULT_MODEL = "gemini-2.5-flash"  # override with GEMINI_MODEL; not verified against the live API yet
_TIMEOUT = httpx.Timeout(connect=5.0, read=20.0, write=5.0, pool=5.0)  # read = max silence between chunks; real policy is M4


class GeminiLLM:
    """Gemini free tier over plain HTTPS + server-sent events. Pieces are forwarded as they arrive (true streaming)."""

    name = "gemini"

    def __init__(self, api_key: str | None = None, model: str | None = None, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._api_key = api_key or os.environ.get("GEMINI_API_KEY")
        if not self._api_key:
            raise LLMError("GEMINI_API_KEY is not set")
        self.model = model or os.environ.get("GEMINI_MODEL", DEFAULT_MODEL)
        self._transport = transport  # tests inject httpx.MockTransport; None = real network

    @staticmethod
    def _payload(messages: list[Message]) -> dict:
        system = "\n".join(m.text for m in messages if m.role == "system")
        contents = [
            {"role": "model" if m.role == "assistant" else "user", "parts": [{"text": m.text}]}
            for m in messages
            if m.role != "system"
        ]
        return {"systemInstruction": {"parts": [{"text": system}]}, "contents": contents}

    async def stream(self, messages: list[Message]) -> AsyncIterator[str]:
        url = f"{BASE_URL}/{self.model}:streamGenerateContent?alt=sse"
        headers = {"x-goog-api-key": self._api_key}  # header, not URL: URLs end up in logs and error messages
        produced = False
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=_TIMEOUT) as client:
                async with client.stream("POST", url, headers=headers, json=self._payload(messages)) as response:
                    if response.status_code != 200:
                        body = (await response.aread()).decode("utf-8", "replace")[:200]
                        raise LLMError(f"Gemini returned HTTP {response.status_code}: {body}")
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue  # blank separators and SSE comments
                        piece = self._extract_text(line[len("data:"):])
                        if piece:
                            produced = True
                            yield piece
        except httpx.HTTPError as exc:  # timeouts, connection errors, dropped streams
            raise LLMError(f"Gemini request failed: {type(exc).__name__}") from exc
        if not produced:
            raise LLMError("Gemini returned an empty reply")  # e.g. blocked by safety filters

    @staticmethod
    def _extract_text(data: str) -> str:
        try:
            event = json.loads(data)
            parts = event["candidates"][0]["content"]["parts"]
        except json.JSONDecodeError as exc:
            raise LLMError("Gemini sent malformed JSON") from exc
        except (KeyError, IndexError, TypeError):
            return ""  # event without text, such as a final usage-only chunk
        return "".join(p.get("text", "") for p in parts)
