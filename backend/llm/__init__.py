import os

from backend.llm.base import LLMError, LLMProvider, Message
from backend.llm.fake import FakeLLM

__all__ = ["LLMError", "LLMProvider", "Message", "FakeLLM", "get_llm_provider"]


def get_llm_provider() -> LLMProvider:
    """Pick the provider from LLM_PROVIDER (default: fake, so demo mode needs no key)."""
    choice = os.environ.get("LLM_PROVIDER", "fake")
    if choice == "fake":
        return FakeLLM()
    if choice == "gemini":
        from backend.llm.gemini import GeminiLLM

        return GeminiLLM()  # raises LLMError if GEMINI_API_KEY is missing
    raise ValueError(f"unknown LLM_PROVIDER {choice!r} (available: fake, gemini)")
