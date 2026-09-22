from datetime import UTC, datetime
from email.message import Message

from lftp_kb import remote_audio
from lftp_kb.models import (
    DiscoveredEpisode,
    EpisodeMatchProposal,
    RemoteAudioObservation,
)


class _Response:
    def __init__(self, headers: Message):
        self.headers = headers

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


def _episode() -> DiscoveredEpisode:
    return DiscoveredEpisode(
        episode_id="rss-one",
        guid="guid-one",
        title="Episode one",
        publication_date=datetime(2020, 1, 2, tzinfo=UTC),
        source_rss_url="https://example.com/feed",
        audio_url="https://media.example.com/show.mp3",
    )


def _proposal() -> EpisodeMatchProposal:
    return EpisodeMatchProposal(
        proposal_id="match-one",
        asset_id="asset-one",
        rss_episode_id="rss-one",
        confidence=0.7,
        reasons=["Filename evidence"],
        recommendation="review",
    )


def test_probe_reads_remote_size_and_headers(monkeypatch):
    headers = Message()
    headers["Content-Length"] = "12345"
    headers["ETag"] = '"abc123"'
    headers["Last-Modified"] = "Mon, 01 Jan 2024 00:00:00 GMT"
    monkeypatch.setattr(
        remote_audio.urllib.request, "urlopen", lambda *_args, **_kwargs: _Response(headers)
    )

    result = remote_audio.probe_remote_audio(_episode())

    assert result.status == "available"
    assert result.content_length == 12345
    assert result.etag == '"abc123"'


def test_equal_sha256_is_required_for_exact_file(tmp_path, monkeypatch):
    archive = tmp_path / "archive"
    archive.mkdir()
    local = archive / "show.mp3"
    local.write_bytes(b"the complete audio bytes")
    downloaded = tmp_path / "remote.mp3"
    downloaded.write_bytes(local.read_bytes())
    monkeypatch.setattr(remote_audio, "_download_remote", lambda _url: downloaded)
    monkeypatch.setattr(remote_audio, "_fpcalc_executable", lambda: None)
    proposal = _proposal()
    observation = RemoteAudioObservation(
        episode_id="rss-one",
        audio_url="https://media.example.com/show.mp3",
        filename="show.mp3",
        probed_at=datetime.now(UTC),
        status="available",
        content_length=local.stat().st_size,
    )

    remote_audio._verify_pair(
        archive,
        proposal,
        "show.mp3",
        local.stat().st_size,
        observation,
    )

    assert proposal.verification_status == "exact-file"
    assert proposal.verification_method == "sha256"
    assert proposal.recommendation == "auto-link"
    assert not downloaded.exists()


def test_same_size_with_different_hash_is_not_exact(tmp_path, monkeypatch):
    archive = tmp_path / "archive"
    archive.mkdir()
    local = archive / "show.mp3"
    local.write_bytes(b"local")
    downloaded = tmp_path / "remote.mp3"
    downloaded.write_bytes(b"other")
    monkeypatch.setattr(remote_audio, "_download_remote", lambda _url: downloaded)
    monkeypatch.setattr(remote_audio, "_fpcalc_executable", lambda: None)
    proposal = _proposal()
    observation = RemoteAudioObservation(
        episode_id="rss-one",
        audio_url="https://media.example.com/show.mp3",
        filename="show.mp3",
        probed_at=datetime.now(UTC),
        status="available",
        content_length=local.stat().st_size,
    )

    remote_audio._verify_pair(
        archive,
        proposal,
        "show.mp3",
        local.stat().st_size,
        observation,
    )

    assert proposal.verification_status == "metadata-only"
    assert proposal.recommendation == "review"
    assert any("hashes differ" in detail for detail in proposal.verification_details)
