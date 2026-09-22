from pathlib import Path

from fastapi.testclient import TestClient

from lftp_kb.config import Settings
from lftp_kb.web import create_app

PROJECT = Path(__file__).resolve().parents[1]


def client():
    settings = Settings(root=PROJECT, rss_url="https://example.com/feed")
    return TestClient(create_app(settings))


def test_dashboard_and_health_render():
    web = client()
    response = web.get("/")
    assert response.status_code == 200
    assert "Editorial command center" in response.text
    health = web.get("/health").json()
    assert health["episodes"] == 3


def test_orientation_pages_render():
    web = client()
    expected = {
        "/episodes": "3 records",
        "/topics": "Controlled vocabulary",
        "/inventory": "Full-stock reconciliation",
        "/settings": "Provider capability matrix",
    }
    for path, marker in expected.items():
        response = web.get(path)
        assert response.status_code == 200
        assert marker in response.text


def test_episode_review_and_outputs_render():
    web = client()
    episode_id = "sample-2026-24-rage-the-elephant"
    response = web.get(f"/episodes/{episode_id}")
    assert response.status_code == 200
    assert "Evidence control" in response.text
    assert web.get(f"/outputs/{episode_id}/json").status_code == 200


def test_missing_episode_is_404():
    assert client().get("/episodes/not-real").status_code == 404
