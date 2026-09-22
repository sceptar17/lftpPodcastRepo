from __future__ import annotations

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


def create_sync_job(repository: Repository, archive_root: Path,
                    episodes: list[DiscoveredEpisode]) -> dict:
    job_id = f"sync-{datetime.now(UTC):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
    payload = {
        "job_id": job_id, "status": "queued", "archive_root": str(archive_root),
        "created_at": datetime.now(UTC).isoformat(), "finished_at": None,
        "total": len(episodes), "completed": 0, "skipped": 0, "failed": 0,
        "items": [
            {
                "episode_id": episode.episode_id, "title": episode.title,
                "audio_url": episode.audio_url, "filename": archive_filename(episode),
                "status": "queued", "message": "",
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
    job["status"] = "running"
    repository.atomic_json(relative, job)
    archive_root = Path(job["archive_root"])
    try:
        archive_root.mkdir(parents=True, exist_ok=True)
        for item in job["items"]:
            item["status"] = "downloading"
            repository.atomic_json(relative, job)
            try:
                outcome = _download_resumable(
                    item["audio_url"], archive_root / item["filename"], timeout
                )
                item["status"] = outcome
                job["skipped" if outcome == "skipped" else "completed"] += 1
            except (OSError, TimeoutError, urllib.error.URLError) as error:
                item["status"] = "failed"
                item["message"] = f"{type(error).__name__}: {error}"
                job["failed"] += 1
            repository.atomic_json(relative, job)
        job["status"] = "complete" if not job["failed"] else "needs-review"
    except OSError as error:
        job["status"] = "failed"
        job["error"] = f"{type(error).__name__}: {error}"
    job["finished_at"] = datetime.now(UTC).isoformat()
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


def latest_sync_job(repository: Repository) -> dict | None:
    directory = repository.root / "state" / "archive-sync"
    paths = sorted(directory.glob("*.json"), reverse=True) if directory.exists() else []
    if not paths:
        return None
    return json.loads(paths[0].read_text(encoding="utf-8"))
