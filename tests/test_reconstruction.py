from datetime import UTC, datetime

from lftp_kb.fingerprinting import _fingerprint_similarity
from lftp_kb.models import DiscoveredEpisode
from lftp_kb.reconstruction import build_reconstruction_report


def _observation(
    path: str, digest: str, *, title: str | None = None, duration: float = 3600.0
) -> dict:
    return {
        "relative_path": path,
        "filename": path.rsplit("/", 1)[-1],
        "size_bytes": 1000,
        "format": "mp3",
        "duration_seconds": duration,
        "title": title,
        "episode_number": None,
        "date": None,
        "date_source": "folder-name",
        "metadata_error": None,
        "filename_episode_number": None,
        "sha256": digest,
    }


def test_reconstruction_separates_annual_and_overall_numbers():
    report = build_reconstruction_report(
        [
            _observation(
                "2023/2023_Episode03_ep495/2023_Episode03_ep495 - 01 Start.mp3",
                "a" * 64,
            )
        ],
        [],
    )

    asset = report.assets[0]
    assert asset.annual_episode_numbers == [3]
    assert asset.overall_episode_numbers == [495]
    assert asset.folder_year == 2023


def test_legacy_filename_guess_does_not_promote_overall_number_to_annual_number():
    observation = _observation("2023/show_ep495.mp3", "f" * 64)
    observation["filename_episode_number"] = 495

    asset = build_reconstruction_report([observation], []).assets[0]

    assert asset.annual_episode_numbers == []
    assert asset.overall_episode_numbers == [495]


def test_exact_copies_are_confirmed_without_listening():
    report = build_reconstruction_report(
        [
            _observation("2010/20100920.mp3", "b" * 64),
            _observation("2010/20100920 (1).mp3", "b" * 64),
        ],
        [],
    )

    proposal = report.duplicate_proposals[0]
    assert proposal.relationship == "exact-copy"
    assert proposal.confidence == 1
    assert proposal.requires_listening is False


def test_independent_number_and_date_signals_create_strong_rss_link():
    episode = DiscoveredEpisode(
        episode_id="ep-002",
        guid="guid-2",
        title="BeliefNet.Org.Com",
        episode_number=2,
        publication_date=datetime(2025, 1, 14, tzinfo=UTC),
        source_rss_url="https://example.com/feed",
        audio_url="https://example.com/e2.mp3",
    )
    report = build_reconstruction_report(
        [
            _observation(
                "2025/20250113Episode02/20250113Episode02 - 01 Start.mp3",
                "c" * 64,
                title="Belief.Net.Org.Com",
            )
        ],
        [episode],
    )

    proposal = report.match_proposals[0]
    assert proposal.rss_episode_id == "ep-002"
    assert proposal.recommendation == "auto-link"
    assert proposal.confidence == 1


def test_import_timestamp_is_not_treated_as_recording_date():
    report = build_reconstruction_report(
        [_observation("2013/20130415 (2016_10_07 00_26_59 UTC).mp3", "d" * 64)], []
    )

    assert report.assets[0].path_dates == ["2013-04-15"]


def test_broadcast_setup_title_is_not_used_for_matching():
    episode = DiscoveredEpisode(
        episode_id="setup",
        guid="setup-guid",
        title="Broadcast Setup",
        publication_date=datetime(2012, 1, 1, tzinfo=UTC),
        source_rss_url="https://example.com/feed",
        audio_url="https://example.com/setup.mp3",
    )
    report = build_reconstruction_report(
        [_observation("2012/mystery.mp3", "e" * 64, title="Broadcast Setup")], [episode]
    )

    assert report.match_proposals == []
    assert "default recording/setup tag" in report.assets[0].metadata_issues[0]


def test_appended_version_is_an_alternate_master_and_preferred():
    report = build_reconstruction_report(
        [
            _observation("2014/20140303 Show.mp3", "1" * 64, duration=3600),
            _observation("2014/20140303 Show A.mp3", "2" * 64, duration=3600),
        ],
        [],
    )

    proposal = report.duplicate_proposals[0]
    assets = {asset.asset_id: asset for asset in report.assets}
    assert proposal.relationship == "alternate-master"
    assert assets[proposal.preferred_asset_id].filename == "20140303 Show A.mp3"
    assert "version marker" in proposal.preference_reasons[0]


def test_copy_increment_before_shared_export_track_is_preferred():
    report = build_reconstruction_report(
        [
            _observation("2023/LFTP_2023_Episode9 - 10.mp3", "3" * 64, duration=3600),
            _observation("2023/LFTP_2023_Episode9_2 - 10.mp3", "4" * 64, duration=3600),
        ],
        [],
    )

    proposal = report.duplicate_proposals[0]
    assets = {asset.asset_id: asset for asset in report.assets}
    assert proposal.relationship == "alternate-master"
    assert assets[proposal.preferred_asset_id].filename == "LFTP_2023_Episode9_2 - 10.mp3"


def test_acoustic_similarity_tolerates_small_fingerprint_changes():
    original = [0xAAAAAAAA] * 100
    adjusted = [0xAAAAAAAB] * 100

    assert _fingerprint_similarity(original, adjusted) > 0.95
