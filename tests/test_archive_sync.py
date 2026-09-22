import json
from datetime import UTC, datetime

from lftp_kb.archive_sync import (
    archive_audio_path,
    archive_filename,
    create_metadata_job,
    create_sync_job,
    execute_metadata_job,
)
from lftp_kb.models import DiscoveredEpisode
from lftp_kb.repository import Repository


def episode() -> DiscoveredEpisode:
    return DiscoveredEpisode(
        episode_id="ep-007-x", guid="guid", title="A Title: With Punctuation!",
        episode_number=7, publication_date=datetime(2026, 9, 22, tzinfo=UTC),
        source_rss_url="https://example.com/feed",
        audio_url="https://example.com/audio.mp3",
    )


def test_archive_filename_is_readable_and_stable():
    assert archive_filename(episode()) == "2026-09-22__ep-0007__a-title-with-punctuation.mp3"


def test_archive_path_uses_publication_year(tmp_path):
    assert archive_audio_path(tmp_path, episode()).parent == tmp_path / "2026"


def test_sync_job_contains_previewed_target(tmp_path):
    job = create_sync_job(Repository(tmp_path), tmp_path / "archive", [episode()])
    assert job["total"] == 1
    assert job["items"][0]["status"] == "queued"


def test_metadata_job_writes_sidecar_checksum_and_manifest(tmp_path):
    repository = Repository(tmp_path / "repository")
    archive = tmp_path / "archive"
    archive.mkdir()
    audio = archive / "existing.mp3"
    audio.write_bytes(b"audio bytes")
    job = create_metadata_job(repository, archive, [(audio, episode(), "test match")])
    execute_metadata_job(repository, job["job_id"])
    sidecar = json.loads((archive / "existing.rss.json").read_text(encoding="utf-8"))
    manifest = json.loads((archive / "archive-manifest.json").read_text(encoding="utf-8"))
    assert sidecar["episode"]["episode_id"] == "ep-007-x"
    assert len(sidecar["archive"]["sha256"]) == 64
    assert manifest["episode_count"] == 1
