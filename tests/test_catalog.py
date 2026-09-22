from datetime import UTC, datetime
from typing import ClassVar

from lftp_kb import catalog
from lftp_kb.catalog import build_episode_ledger, inspect_audio_file
from lftp_kb.inventory import InventorySnapshot, LocalAudioItem
from lftp_kb.models import DiscoveredEpisode
from lftp_kb.repository import Repository


class _Media:
    tags: ClassVar[dict[str, list[str]]] = {
        "title": ["A trustworthy old episode"],
        "tracknumber": ["47/500"],
        "date": ["2011-04-03"],
    }
    info = None


def test_embedded_episode_number_is_high_confidence(tmp_path, monkeypatch):
    archive = tmp_path / "archive"
    path = archive / "2011" / "old-show.mp3"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"audio placeholder")
    monkeypatch.setattr(catalog, "MutagenFile", lambda *_args, **_kwargs: _Media())

    result = inspect_audio_file(path, archive)

    assert result["episode_number"] == 47
    assert result["title"] == "A trustworthy old episode"
    assert result["date"] == "2011-04-03"


def test_folder_year_is_retained_when_tags_have_no_date(tmp_path, monkeypatch):
    archive = tmp_path / "archive"
    path = archive / "2010" / "mystery.mp3"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"audio placeholder")
    monkeypatch.setattr(catalog, "MutagenFile", lambda *_args, **_kwargs: None)

    result = inspect_audio_file(path, archive)

    assert result["date"] == "2010"
    assert result["date_precision"] == "year"
    assert result["date_source"] == "folder-name"


def test_unreadable_file_becomes_auditable_issue(tmp_path, monkeypatch):
    archive = tmp_path / "archive"
    path = archive / "2010" / "unreadable.mp3"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"audio placeholder")
    monkeypatch.setattr(
        catalog,
        "_sha256_file",
        lambda _path: (_ for _ in ()).throw(OSError(22, "Invalid argument")),
    )
    monkeypatch.setattr(catalog, "MutagenFile", lambda *_args, **_kwargs: None)

    result = inspect_audio_file(path, archive)

    assert result["sha256"] is None
    assert "Invalid argument" in result["file_errors"][0]


def test_ledger_keeps_embedded_track_number_as_evidence(tmp_path, monkeypatch):
    repository = Repository(tmp_path / "repo")
    archive = tmp_path / "archive"
    audio = archive / "2012" / "known.mp3"
    audio.parent.mkdir(parents=True)
    audio.write_bytes(b"audio placeholder")
    monkeypatch.setattr(catalog, "MutagenFile", lambda *_args, **_kwargs: _Media())
    episode = DiscoveredEpisode(
        episode_id="rss-known",
        guid="guid-known",
        title="Known show",
        publication_date=datetime(2012, 5, 1, tzinfo=UTC),
        source_rss_url="https://example.com/feed",
        audio_url="https://example.com/known.mp3",
    )
    local = LocalAudioItem(
        path=str(audio),
        filename=audio.name,
        size_mb=0.0,
        guessed_episode_number=None,
        match_episode_id=episode.episode_id,
        match_reason="Exact RSS enclosure filename",
        match_confidence=1.0,
        match_status="matched",
    )
    snapshot = InventorySnapshot(
        discovered=[episode],
        processed=[],
        missing_from_repository=[episode],
        local_audio=[local],
        unmatched_local_audio=[],
        uncertain_local_audio=[],
        feed_only_audio=[],
        matched_feed_count=1,
        local_root_configured=True,
        local_root_exists=True,
    )

    ledger = build_episode_ledger(repository, snapshot, archive)

    assert ledger.candidates[0].episode_number is None
    assert ledger.candidates[0].episode_number_confidence == "unknown"
    assert any(
        evidence.field == "track_number" and evidence.value == "47"
        for evidence in ledger.candidates[0].evidence
    )
    assert (repository.root / "catalog" / "master-ledger.json").exists()
    assert (repository.root / "catalog" / "master-ledger.csv").exists()


def test_legacy_number_only_inventory_match_is_not_accepted(tmp_path, monkeypatch):
    repository = Repository(tmp_path / "repo")
    archive = tmp_path / "archive"
    audio = archive / "2011" / "ep495.mp3"
    audio.parent.mkdir(parents=True)
    audio.write_bytes(b"audio placeholder")
    monkeypatch.setattr(catalog, "MutagenFile", lambda *_args, **_kwargs: _Media())
    episode = DiscoveredEpisode(
        episode_id="rss-495",
        guid="guid-495",
        title="A different episode",
        episode_number=495,
        publication_date=datetime(2020, 5, 1, tzinfo=UTC),
        source_rss_url="https://example.com/feed",
        audio_url="https://example.com/different.mp3",
    )
    local = LocalAudioItem(
        path=str(audio),
        filename=audio.name,
        size_mb=0.0,
        guessed_episode_number=495,
        match_episode_id=episode.episode_id,
        match_reason="Unique episode number 495",
        match_confidence=1.0,
        match_status="matched",
    )
    snapshot = InventorySnapshot(
        discovered=[episode],
        processed=[],
        missing_from_repository=[episode],
        local_audio=[local],
        unmatched_local_audio=[],
        uncertain_local_audio=[],
        feed_only_audio=[],
        matched_feed_count=1,
        local_root_configured=True,
        local_root_exists=True,
    )

    ledger = build_episode_ledger(repository, snapshot, archive)

    assert ledger.candidate_count == 2
    rss_candidate = next(item for item in ledger.candidates if item.sources.rss_episode_id)
    local_candidate = next(item for item in ledger.candidates if item.sources.local_files)
    assert rss_candidate.sources.local_files == []
    assert local_candidate.overall_episode_number == 495
    assert local_candidate.episode_number is None
