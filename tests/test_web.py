import json
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from lftp_kb.config import Settings
from lftp_kb.models import CatalogAsset, DuplicateProposal, ReconstructionReport
from lftp_kb.repository import Repository
from lftp_kb.web import create_app, pretty_date

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
        "/catalog": "One reference list",
        "/settings": "Provider capability matrix",
        "/transcription-lab": "Test one excerpt first",
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


def test_date_format_is_cross_platform():
    value = datetime(2026, 9, 2, tzinfo=UTC)
    assert pretty_date(value) == "Sep 2, 2026"
    assert pretty_date(value, "long") == "September 2, 2026"


def test_benchmark_rejects_missing_audio():
    response = client().post(
        "/transcription-lab/run",
        data={
            "audio_path": "Z:/not-here.mp3",
            "model": "small.en",
            "sample_minutes": "10",
        },
    )
    assert response.status_code == 400


def test_duplicate_decision_records_the_file_to_keep(tmp_path):
    repository = Repository(tmp_path)
    assets = [
        CatalogAsset(
            asset_id="asset-first",
            relative_path="2023/show.mp3",
            filename="show.mp3",
            size_bytes=100,
            format="mp3",
        ),
        CatalogAsset(
            asset_id="asset-second",
            relative_path="2023/show_2.mp3",
            filename="show_2.mp3",
            size_bytes=101,
            format="mp3",
        ),
    ]
    report = ReconstructionReport(
        generated_at=datetime.now(UTC),
        assets=assets,
        duplicate_proposals=[
            DuplicateProposal(
                proposal_id="duplicate-test",
                relationship="alternate-master",
                asset_ids=["asset-first", "asset-second"],
                confidence=0.9,
                reasons=["Version suffix"],
                requires_listening=True,
                preferred_asset_id="asset-second",
            )
        ],
        match_proposals=[],
    )
    repository.atomic_json("catalog/reconstruction-report.json", report.model_dump(mode="json"))
    web = TestClient(create_app(Settings(root=tmp_path, rss_url="https://example.com/feed")))

    response = web.post(
        "/catalog/proposals/duplicate-test",
        data={
            "action": "confirmed",
            "preferred_asset_id": "asset-second",
        },
        follow_redirects=False,
    )

    decisions = json.loads(
        (tmp_path / "catalog" / "reconstruction-decisions.json").read_text(encoding="utf-8")
    )
    assert response.status_code == 303
    assert decisions["decisions"]["duplicate-test"]["preferred_asset_id"] == "asset-second"
