from datetime import UTC, datetime

from lftp_kb.archive_sync import archive_filename, create_sync_job
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


def test_sync_job_contains_previewed_target(tmp_path):
    job = create_sync_job(Repository(tmp_path), tmp_path / "archive", [episode()])
    assert job["total"] == 1
    assert job["items"][0]["status"] == "queued"
