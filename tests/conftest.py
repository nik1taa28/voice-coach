"""Keep the tests independent of the shell they run in.

The app reads its settings from environment variables. A developer who ran `source .env` has LLM_PROVIDER=gemini,
ASR_PROVIDER=whisper and a real API key exported, so any test that asks for the default provider would silently use the
real services: slow, flaky, and it spends free-tier quota. Tests that need a setting set it themselves (monkeypatch.setenv).
"""
import os

import pytest

_APP_SETTINGS = (
    "ASR_PROVIDER", "ASR_MODEL", "LLM_PROVIDER", "GEMINI_API_KEY", "GEMINI_MODEL", "TTS_PROVIDER", "FAKE_LLM_DELAY_S",
    "FOLLOWUPS_PER_QUESTION", "INTERVIEW_TIME_LIMIT_S", "METRICS_WINDOW",
)


def _app_variables() -> list[str]:
    return [name for name in os.environ if name in _APP_SETTINGS or name.startswith("TIMEOUT_")]


# At import: backend.main reads some settings once when it is imported, which happens after this file loads.
for _name in _app_variables():
    del os.environ[_name]


@pytest.fixture(autouse=True)
def _clean_app_environment(monkeypatch):
    # Per test: also covers a test that sets a variable and a later test that would otherwise inherit it.
    for name in _app_variables():
        monkeypatch.delenv(name)
