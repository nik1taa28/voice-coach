from fastapi.testclient import TestClient

from backend.main import app

client = TestClient(app)


def test_index_serves_the_page():
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Voice Coach" in response.text
