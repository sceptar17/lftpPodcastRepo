import json
from datetime import UTC, datetime

from lftp_kb.inventory import build_inventory, scan_local_audio
from lftp_kb.models import DiscoveredEpisode
from lftp_kb.repository import Repository


def discovered(episode_id="ep-007-x", episode_number=7):
    return DiscoveredEpisode(
        episode_id=episode_id,
        guid="guid",
        title="A title",
        episode_number=episode_number,
        publication_date=datetime.now(UTC),
        source_rss_url="https://example.com/feed",
        audio_url="https://example.com/audio.mp3",
    )


def test_local_audio_scan_matches_episode_number(tmp_path):
    audio = tmp_path / "LFTP Episode 7 final.mp3"
    audio.write_bytes(b"audio")
    items = scan_local_audio(tmp_path, [discovered()])
    assert items[0].guessed_episode_number == 7
    assert items[0].match_episode_id == "ep-007-x"


def test_inventory_loads_feed_snapshot(tmp_path):
    repository = Repository(tmp_path)
    (tmp_path / "state" / "discovered.json").write_text(
        json.dumps([discovered().model_dump(mode="json")]),
        encoding="utf-8",
    )
    snapshot = build_inventory(repository, None)
    assert len(snapshot.discovered) == 1
    assert len(snapshot.missing_from_repository) == 1
