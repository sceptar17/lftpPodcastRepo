from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from mutagen import File as MutagenFile
from mutagen import MutagenError

from .fingerprinting import _fingerprint_similarity, _fpcalc
from .models import (
    DiscoveredEpisode,
    EpisodeMatchProposal,
    ReconstructionReport,
    RemoteAudioObservation,
)
from .repository import Repository

VerificationProgress = Callable[[int, int, str, str | None], None]


def verify_remote_audio(
    repository: Repository,
    archive_root: Path,
    report: ReconstructionReport,
    episodes: list[DiscoveredEpisode],
    decisions: dict,
    progress: VerificationProgress | None = None,
) -> ReconstructionReport:
    episode_map = {episode.episode_id: episode for episode in episodes}
    asset_map = {asset.asset_id: asset for asset in report.assets}
    cached_probes = _load_probe_cache(repository)
    acoustic_available = _fpcalc_executable() is not None
    candidates = [
        proposal
        for proposal in report.match_proposals
        if decisions.get(proposal.proposal_id, {}).get("decision") not in {"confirmed", "rejected"}
        and proposal.verification_status
        not in {"exact-file", "same-recording", "different-recording"}
        and (proposal.verification_status != "metadata-only" or acoustic_available)
    ]
    total = len(candidates)
    for index, proposal in enumerate(candidates, start=1):
        asset = asset_map[proposal.asset_id]
        episode = episode_map[proposal.rss_episode_id]
        if progress:
            progress(index - 1, total, "Verifying local audio against RSS audio", asset.filename)
        observation = cached_probes.get(episode.episode_id)
        if observation is None or observation.audio_url != episode.audio_url:
            observation = probe_remote_audio(episode)
            cached_probes[episode.episode_id] = observation
            _save_probe_cache(repository, cached_probes)
        try:
            _verify_pair(
                archive_root,
                proposal,
                asset.relative_path,
                asset.size_bytes,
                observation,
            )
        except Exception as error:  # noqa: BLE001 - one bad object must not stop the batch
            proposal.verified_at = datetime.now(UTC)
            proposal.verification_status = "failed"
            proposal.verification_method = "audio-verification"
            proposal.verification_details = [f"{type(error).__name__}: {error}"]
        _save_verification(repository, report)
        if progress:
            progress(index, total, "Verifying local audio against RSS audio", asset.filename)
    _save_probe_cache(repository, cached_probes)
    _save_verification(repository, report)
    return report


def apply_cached_verification(repository: Repository, report: ReconstructionReport) -> None:
    path = repository.root / "catalog" / "audio-verification.json"
    if not path.exists():
        return
    try:
        records = json.loads(path.read_text(encoding="utf-8")).get("proposals", {})
    except (json.JSONDecodeError, OSError):
        return
    for proposal in report.match_proposals:
        record = records.get(proposal.proposal_id)
        if not record:
            continue
        for field in (
            "verification_status",
            "verification_method",
            "verification_details",
            "remote_size_bytes",
            "remote_duration_seconds",
            "acoustic_similarity",
            "verified_at",
        ):
            if field in record:
                value = record[field]
                if field == "verified_at" and isinstance(value, str):
                    value = datetime.fromisoformat(value)
                setattr(proposal, field, value)
        if proposal.verification_status in {"exact-file", "same-recording"}:
            proposal.recommendation = "auto-link"


def probe_remote_audio(episode: DiscoveredEpisode, timeout: int = 20) -> RemoteAudioObservation:
    filename = Path(urllib.parse.unquote(urllib.parse.urlparse(episode.audio_url).path)).name
    headers = None
    error = None
    try:
        request = urllib.request.Request(
            episode.audio_url, method="HEAD", headers={"User-Agent": "lftp-knowledge/0.2"}
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            headers = response.headers
    except (OSError, urllib.error.URLError, urllib.error.HTTPError) as head_error:
        error = f"HEAD failed: {type(head_error).__name__}: {head_error}"
        try:
            request = urllib.request.Request(
                episode.audio_url,
                headers={"User-Agent": "lftp-knowledge/0.2", "Range": "bytes=0-0"},
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:
                headers = response.headers
        except (OSError, urllib.error.URLError, urllib.error.HTTPError) as range_error:
            error = f"{error}; range probe failed: {type(range_error).__name__}: {range_error}"
    if headers is None:
        return RemoteAudioObservation(
            episode_id=episode.episode_id,
            audio_url=episode.audio_url,
            filename=filename,
            probed_at=datetime.now(UTC),
            status="failed",
            error=error,
        )
    content_length = _content_length(headers)
    return RemoteAudioObservation(
        episode_id=episode.episode_id,
        audio_url=episode.audio_url,
        filename=filename,
        probed_at=datetime.now(UTC),
        status="available",
        content_length=content_length,
        etag=headers.get("ETag"),
        last_modified=headers.get("Last-Modified"),
        accept_ranges=headers.get("Accept-Ranges"),
        content_type=headers.get("Content-Type"),
    )


def _verify_pair(
    archive_root: Path,
    proposal: EpisodeMatchProposal,
    relative_path: str,
    local_size: int,
    remote: RemoteAudioObservation,
) -> None:
    proposal.verified_at = datetime.now(UTC)
    proposal.remote_size_bytes = remote.content_length
    if remote.status != "available":
        proposal.verification_status = "failed"
        proposal.verification_method = "http-probe"
        proposal.verification_details = [remote.error or "Remote audio was unavailable."]
        return
    details = []
    if remote.content_length is not None:
        if remote.content_length == local_size:
            details.append("Remote and local byte sizes match.")
        else:
            details.append(
                f"Byte sizes differ: local {local_size}, remote {remote.content_length}."
            )
    should_hash = remote.content_length == local_size
    temporary = _download_remote(remote.audio_url)
    try:
        proposal.remote_duration_seconds = _media_duration(temporary)
        if proposal.remote_duration_seconds is not None:
            details.append(
                f"Measured remote duration is {proposal.remote_duration_seconds:.1f} seconds."
            )
        if should_hash:
            local_sha256 = _file_digest(archive_root / relative_path, "sha256")
            remote_sha256 = _file_digest(temporary, "sha256")
            if local_sha256 == remote_sha256:
                proposal.verification_status = "exact-file"
                proposal.verification_method = "sha256"
                proposal.verification_details = [
                    *details,
                    "A complete SHA-256 comparison proves the files are byte-for-byte identical.",
                ]
                proposal.recommendation = "auto-link"
                return
            details.append("Complete SHA-256 hashes differ, so these are not exact byte copies.")
        executable = _fpcalc_executable()
        if executable is None:
            proposal.verification_status = "metadata-only"
            proposal.verification_method = "sha256"
            proposal.verification_details = [
                *details,
                "Chromaprint fpcalc is unavailable, so alternate encodings were not compared.",
            ]
            return
        local_fingerprint = _fpcalc(executable, archive_root / relative_path)
        remote_fingerprint = _fpcalc(executable, temporary)
        similarity = _fingerprint_similarity(local_fingerprint, remote_fingerprint)
        proposal.acoustic_similarity = similarity
        proposal.verification_method = "chromaprint"
        proposal.verification_details = [*details, f"Acoustic similarity is {similarity:.1%}."]
        if similarity >= 0.82:
            proposal.verification_status = "same-recording"
            proposal.recommendation = "auto-link"
        elif similarity < 0.6:
            proposal.verification_status = "different-recording"
        else:
            proposal.verification_status = "metadata-only"
    finally:
        temporary.unlink(missing_ok=True)


def _download_remote(url: str, timeout: int = 120) -> Path:
    suffix = Path(urllib.parse.urlparse(url).path).suffix or ".audio"
    target: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as handle:
            target = Path(handle.name)
            request = urllib.request.Request(url, headers={"User-Agent": "lftp-knowledge/0.2"})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                shutil.copyfileobj(response, handle, length=1024 * 1024)
        return target
    except Exception:
        if target is not None:
            target.unlink(missing_ok=True)
        raise


def _fpcalc_executable() -> str | None:
    configured = os.getenv("LFTP_FPCALC_PATH")
    return configured if configured and Path(configured).is_file() else shutil.which("fpcalc")


def _file_digest(path: Path, algorithm: str) -> str:
    digest = hashlib.new(algorithm, usedforsecurity=False)
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _media_duration(path: Path) -> float | None:
    try:
        media = MutagenFile(path)
        length = getattr(getattr(media, "info", None), "length", None)
        return round(float(length), 3) if length is not None else None
    except (MutagenError, OSError, ValueError):
        return None


def _content_length(headers) -> int | None:
    content_range = headers.get("Content-Range", "")
    if match := re.search(r"/(\d+)$", content_range):
        return int(match.group(1))
    value = headers.get("Content-Length", "")
    return int(value) if value.isdigit() else None


def _load_probe_cache(repository: Repository) -> dict[str, RemoteAudioObservation]:
    path = repository.root / "catalog" / "observations" / "remote-audio.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return {
            item["episode_id"]: RemoteAudioObservation.model_validate(item)
            for item in payload.get("episodes", [])
        }
    except (json.JSONDecodeError, OSError, ValueError):
        return {}


def _save_probe_cache(
    repository: Repository, observations: dict[str, RemoteAudioObservation]
) -> None:
    repository.atomic_json(
        "catalog/observations/remote-audio.json",
        {
            "schema_version": "1.0.0",
            "updated_at": datetime.now(UTC).isoformat(),
            "episodes": [item.model_dump(mode="json") for item in observations.values()],
        },
    )


def _save_verification(repository: Repository, report: ReconstructionReport) -> None:
    repository.atomic_json(
        "catalog/audio-verification.json",
        {
            "schema_version": "1.0.0",
            "updated_at": datetime.now(UTC).isoformat(),
            "proposals": {
                proposal.proposal_id: {
                    "verification_status": proposal.verification_status,
                    "verification_method": proposal.verification_method,
                    "verification_details": proposal.verification_details,
                    "remote_size_bytes": proposal.remote_size_bytes,
                    "remote_duration_seconds": proposal.remote_duration_seconds,
                    "acoustic_similarity": proposal.acoustic_similarity,
                    "verified_at": (
                        proposal.verified_at.isoformat() if proposal.verified_at else None
                    ),
                }
                for proposal in report.match_proposals
                if proposal.verification_status != "not-checked"
            },
        },
    )
    repository.atomic_json("catalog/reconstruction-report.json", report.model_dump(mode="json"))
