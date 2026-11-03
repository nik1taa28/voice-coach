from fastapi.testclient import TestClient

from backend.main import app

client = TestClient(app)


def test_index_serves_the_page():
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Voice Coach" in response.text


def test_index_has_interview_controls():
    html = client.get("/").text
    for element_id in ("topic", "difficulty", "start", "toggle", "pause", "resume", "end", "summary"):
        assert f'id="{element_id}"' in html


def test_index_handles_speech_messages():
    # Only checks the wiring strings exist; the real behaviour is verified by hand in a browser.
    html = client.get("/").text
    for token in ("msg.type === 'speak'", "speechSynthesis", "playback_started", "cancelSpeech"):
        assert token in html


def test_topic_is_a_dropdown_filled_from_the_server():
    html = client.get("/").text
    assert '<select id="topic"' in html and "fetch('/topics')" in html


def test_a_bank_line_does_not_clear_the_fake_llm_warning():
    # A bank question makes no LLM call, so it must not count as "the LLM is real". Same rule as for provider 'none'.
    html = client.get("/").text
    assert "provider === 'none' || provider === 'bank'" in html
