"""M5: the dashboard page is served and cannot silently drift from what /metrics reports. Layout needs a browser (by hand)."""
from fastapi.testclient import TestClient

from backend.main import app
from backend.metrics import COUNTERS, STAGES

client = TestClient(app)


def test_dashboard_is_served_as_html():
    response = client.get("/dashboard")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert 'fetch("/metrics"' in response.text


def test_every_stage_has_a_row():
    html = client.get("/dashboard").text
    for stage in STAGES:
        assert f'data-stage="{stage}"' in html, stage


def test_every_counter_has_a_tile():
    html = client.get("/dashboard").text
    for name in COUNTERS:
        assert f'data-count="{name}"' in html, name


def test_dashboard_reads_the_real_snapshot_keys():
    snap = client.get("/metrics").json()
    html = client.get("/dashboard").text
    for key in ("stages", "counts", "error_rate", "window"):
        assert key in snap and f"snap.{key}" in html, key
