from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import UTC, datetime
from pathlib import Path

from .models import DiscoveredEpisode
from .repository import Repository


def archive_filename(episode: DiscoveredEpisode) -> str:
    extension = Path(urllib.parse.urlparse(episode.audio_url).path).suffix.lower()
    if not extension or len(extension) > 6:
        extension = ".mp3"
    slug = "-".join(re.findall(r"[a-z0-9]+", episode.title.lower()))[:90] or episode.episode_id
    number = f"ep-{episode.episode_number:04d}__" if episode.episode_number is not None else ""
    return f"{episode.publication_date:%Y-%m-%d}__{number}{slug}{extension}"


def archive_audio_path(archive_root: Path, episode: DiscoveredEpisode) -> Path:
    return archive_root / str(episode.publication_date.year) / archive_filename(episode)


def create_sync_job(repository: Repository, archive_root: Path,
                    episodes: list[DiscoveredEpisode]) -> dict:
    job_id = f"sync-{datetime.now(UTC):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
    payload = {
        "job_id": job_id, "operation": "download", "status": "queued",
        "archive_root": str(archive_root),
        "created_at": datetime.now(UTC).isoformat(), "started_at": None,
        "updated_at": None, "finished_at": None, "stage": "Waiting to start",
        "current_label": None, "processed": 0,
        "total": len(episodes), "completed": 0, "skipped": 0, "failed": 0,
        "items": [
            {
                "episode_id": episode.episode_id, "title": episode.title,
                "audio_url": episode.audio_url, "filename": archive_filename(episode),
                "year": episode.publication_date.year,
                "episode": episode.model_dump(mode="json"),
                "status": "queued", "message": "", "sidecar": None,
            }
            for episode in episodes
        ],
    }
    repository.atomic_json(f"state/archive-sync/{job_id}.json", payload)
    return payload


def execute_sync_job(repository: Repository, job_id: str, timeout: int = 120) -> None:
    relative = f"state/archive-sync/{job_id}.json"
    path = repository.root / relative
    job = json.loads(path.read_text(encoding="utf-8"))
    job.update(status="running", started_at=datetime.now(UTC).isoformat(),
               updated_at=datetime.now(UTC).isoformat(), stage="Downloading RSS audio")
    repository.atomic_json(relative, job)
    archive_root = Path(job["archive_root"])
    try:
        archive_root.mkdir(parents=True, exist_ok=True)
        for item in job["items"]:
            item["status"] = "downloading"
            job["current_label"] = item["title"]
            repository.atomic_json(relative, job)
            try:
                episode = DiscoveredEpisode.model_validate(item["episode"])
                target = archive_audio_path(archive_root, episode)
                target.parent.mkdir(parents=True, exist_ok=True)
                outcome = _download_resumable(item["audio_url"], target, timeout)
                sidecar = _write_sidecar(
                    archive_root, target, episode,
                    archive_source="rss-download", match_reason="RSS enclosure download",
                )
                item["sidecar"] = str(sidecar.relative_to(archive_root))
                item["status"] = outcome
                job["skipped" if outcome == "skipped" else "completed"] += 1
            except (OSError, TimeoutError, urllib.error.URLError) as error:
                item["status"] = "failed"
                item["message"] = f"{type(error).__name__}: {error}"
                job["failed"] += 1
            job["processed"] += 1
            job["updated_at"] = datetime.now(UTC).isoformat()
            repository.atomic_json(relative, job)
        job["stage"] = "Writing archive manifest"
        job["current_label"] = None
        _write_manifest(archive_root)
        job["status"] = "complete" if not job["failed"] else "needs-review"
    except OSError as error:
        job["status"] = "failed"
        job["error"] = f"{type(error).__name__}: {error}"
    job["finished_at"] = datetime.now(UTC).isoformat()
    job["updated_at"] = job["finished_at"]
    repository.atomic_json(relative, job)


def _download_resumable(url: str, target: Path, timeout: int) -> str:
    if target.exists() and target.stat().st_size > 0:
        return "skipped"
    partial = target.with_suffix(target.suffix + ".part")
    offset = partial.stat().st_size if partial.exists() else 0
    headers = {"User-Agent": "lftp-knowledge/0.3"}
    if offset:
        headers["Range"] = f"bytes={offset}-"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        append = offset > 0 and getattr(response, "status", None) == 206
        mode = "ab" if append else "wb"
        with partial.open(mode) as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
    partial.replace(target)
    return "downloaded"


def create_metadata_job(repository: Repository, archive_root: Path,
                        matches: list[tuple[Path, DiscoveredEpisode, str]]) -> dict:
    job_id = f"metadata-{datetime.now(UTC):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
    payload = {
        "job_id": job_id, "operation": "metadata", "status": "queued",
        "archive_root": str(archive_root), "created_at": datetime.now(UTC).isoformat(),
        "started_at": None, "updated_at": None, "finished_at": None,
        "stage": "Waiting to start", "current_label": None, "processed": 0,
        "total": len(matches), "completed": 0,
        "skipped": 0, "failed": 0,
        "items": [
            {
                "episode_id": episode.episode_id, "title": episode.title,
                "audio_path": str(audio_path), "episode": episode.model_dump(mode="json"),
                "match_reason": reason, "status": "queued", "message": "", "sidecar": None,
            }
            for audio_path, episode, reason in matches
        ],
    }
    repository.atomic_json(f"state/archive-sync/{job_id}.json", payload)
    return payload


def execute_metadata_job(repository: Repository, job_id: str) -> None:
    relative = f"state/archive-sync/{job_id}.json"
    job = json.loads((repository.root / relative).read_text(encoding="utf-8"))
    job.update(status="running", started_at=datetime.now(UTC).isoformat(),
               updated_at=datetime.now(UTC).isoformat(), stage="Writing metadata sidecars")
    repository.atomic_json(relative, job)
    archive_root = Path(job["archive_root"])
    for item in job["items"]:
        item["status"] = "writing-metadata"
        job["current_label"] = item["title"]
        repository.atomic_json(relative, job)
        try:
            audio_path = Path(item["audio_path"])
            sidecar_path = audio_path.with_suffix(".rss.json")
            if sidecar_path.exists():
                item["status"] = "skipped"
                job["skipped"] += 1
            else:
                sidecar = _write_sidecar(
                    archive_root, audio_path,
                    DiscoveredEpisode.model_validate(item["episode"]),
                    archive_source="matched-existing",
                    match_reason=item["match_reason"],
                )
                item["sidecar"] = str(sidecar.relative_to(archive_root))
                item["status"] = "created"
                job["completed"] += 1
        except (OSError, ValueError) as error:
            item["status"] = "failed"
            item["message"] = f"{type(error).__name__}: {error}"
            job["failed"] += 1
        job["processed"] += 1
        job["updated_at"] = datetime.now(UTC).isoformat()
        repository.atomic_json(relative, job)
    try:
        job["stage"] = "Writing archive manifest"
        job["current_label"] = None
        _write_manifest(archive_root)
    except OSError as error:
        job["failed"] += 1
        job["manifest_error"] = f"{type(error).__name__}: {error}"
    job["status"] = "complete" if not job["failed"] else "needs-review"
    job["finished_at"] = datetime.now(UTC).isoformat()
    job["updated_at"] = job["finished_at"]
    repository.atomic_json(relative, job)


def _write_sidecar(archive_root: Path, audio_path: Path, episode: DiscoveredEpisode,
                   archive_source: str, match_reason: str) -> Path:
    relative_audio = audio_path.relative_to(archive_root)
    sidecar_path = audio_path.with_suffix(".rss.json")
    payload = {
        "archive_schema_version": "1.0.0",
        "episode": episode.model_dump(mode="json"),
        "archive": {
            "source": archive_source,
            "match_reason": match_reason,
            "audio_relative_path": relative_audio.as_posix(),
            "audio_filename": audio_path.name,
            "size_bytes": audio_path.stat().st_size,
            "sha256": _sha256_file(audio_path),
            "metadata_created_at": datetime.now(UTC).isoformat(),
        },
    }
    _atomic_external_json(sidecar_path, payload)
    return sidecar_path


def _write_manifest(archive_root: Path) -> Path:
    entries = []
    for path in sorted(archive_root.rglob("*.rss.json")):
        try:
            sidecar = json.loads(path.read_text(encoding="utf-8"))
            entries.append({
                "episode_id": sidecar["episode"]["episode_id"],
                "title": sidecar["episode"]["title"],
                "publication_date": sidecar["episode"]["publication_date"],
                "audio_relative_path": sidecar["archive"]["audio_relative_path"],
                "sidecar_relative_path": path.relative_to(archive_root).as_posix(),
                "sha256": sidecar["archive"]["sha256"],
            })
        except (KeyError, json.JSONDecodeError, OSError, ValueError):
            continue
    target = archive_root / "archive-manifest.json"
    _atomic_external_json(target, {
        "archive_schema_version": "1.0.0",
        "generated_at": datetime.now(UTC).isoformat(),
        "episode_count": len(entries), "episodes": entries,
    })
    return target


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_external_json(target: Path, payload: dict) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(target)


def latest_sync_job(repository: Repository) -> dict | None:
    directory = repository.root / "state" / "archive-sync"
    paths = list(directory.glob("*.json")) if directory.exists() else []
    if not paths:
        return None
    jobs = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    return max(jobs, key=lambda job: job["created_at"])
