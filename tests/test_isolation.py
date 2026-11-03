"""The test suite must not depend on the shell it runs in (see conftest.py)."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_a_developer_shell_with_real_providers_exported_does_not_change_test_results():
    # The settings a developer has after `source .env`, with a dummy key so nothing real can ever be called.
    hostile = {
        **os.environ,
        "LLM_PROVIDER": "gemini", "ASR_PROVIDER": "whisper", "TTS_PROVIDER": "browser",
        "GEMINI_API_KEY": "dummy-not-a-real-key", "TIMEOUT_LLM_FIRST_S": "0.001", "FOLLOWUPS_PER_QUESTION": "0",
    }
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/test_asr_ws.py", "tests/test_llm_ws.py"],
        cwd=ROOT, env=hostile, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stdout[-1500:] + result.stderr[-500:]
