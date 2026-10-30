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
